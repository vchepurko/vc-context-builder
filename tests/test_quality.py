"""Tests for the Phase-2 quality detectors.

Each detector is exercised directly against synthetic JSONL entries
so the tests stay fast and focused.  An end-to-end test wires the
real ``Dispatcher`` + ``MetricsWriter`` and asserts that
``QueryEngine.get_session_metrics(quality=True)`` surfaces the
expected findings.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from mcp.dispatcher import Dispatcher  # noqa: E402
from mcp.metrics import MetricsWriter, _args_summary  # noqa: E402
from mcp.quality import (  # noqa: E402
    detect_empty_streaks,
    detect_hot_rereads,
    detect_wasteful_pairs,
    quality_report,
)
from query_engine import QueryEngine  # noqa: E402


def _entry(
    *,
    tool: str,
    ts: str = "2026-05-07T10:00:00+00:00",
    args_keys=None,
    args_summary=None,
    ok: bool = True,
    empty: bool = False,
    result_bytes: int = 100,
) -> dict:
    return {
        "ts": ts, "tool": tool,
        "args_keys": list(args_keys or []),
        "args_summary": dict(args_summary or {}),
        "result_bytes": result_bytes,
        "approx_tokens": result_bytes // 4,
        "t_ms": 1, "ok": ok, "empty": empty,
    }


def _ts(seconds_offset: int) -> str:
    base = _dt.datetime(2026, 5, 7, 10, 0, 0, tzinfo=_dt.timezone.utc)
    return (base + _dt.timedelta(seconds=seconds_offset)).isoformat(
        timespec="seconds",
    )


class ArgsSummaryTests(unittest.TestCase):
    def test_picks_known_value_keys(self) -> None:
        out = _args_summary({
            "name": "QueryEngine", "fields": ["file"],
            "include_body": True, "garbage": object(),
        })
        self.assertEqual(out, {"name": "QueryEngine"})

    def test_clamps_long_strings(self) -> None:
        out = _args_summary({"pattern": "x" * 500})
        self.assertEqual(len(out["pattern"]), 100)

    def test_empty_input(self) -> None:
        self.assertEqual(_args_summary(None), {})
        self.assertEqual(_args_summary({}), {})


class WastefulPairsTests(unittest.TestCase):
    def test_finds_pair_within_window(self) -> None:
        entries = [
            _entry(tool="find_symbol", ts=_ts(0),
                   args_keys=["name"], args_summary={"name": "X"}),
            _entry(tool="read_slice", ts=_ts(10),
                   args_keys=["file", "start", "end"],
                   args_summary={"file": "pkg/x.py"}),
        ]
        findings = detect_wasteful_pairs(entries)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["symbol"], "X")
        self.assertEqual(findings[0]["kind"], "wasteful_pair")

    def test_skip_when_include_body_passed(self) -> None:
        entries = [
            _entry(tool="find_symbol", ts=_ts(0),
                   args_keys=["name", "include_body"],
                   args_summary={"name": "X"}),
            _entry(tool="read_slice", ts=_ts(5),
                   args_summary={"file": "pkg/x.py"}),
        ]
        self.assertEqual(detect_wasteful_pairs(entries), [])

    def test_skip_when_outside_window(self) -> None:
        entries = [
            _entry(tool="find_symbol", ts=_ts(0),
                   args_keys=["name"], args_summary={"name": "X"}),
            _entry(tool="read_slice", ts=_ts(120),
                   args_summary={"file": "pkg/x.py"}),
        ]
        self.assertEqual(detect_wasteful_pairs(entries, window_sec=60), [])

    def test_intervening_call_resets(self) -> None:
        entries = [
            _entry(tool="find_symbol", ts=_ts(0),
                   args_keys=["name"], args_summary={"name": "X"}),
            _entry(tool="who_calls", ts=_ts(5),
                   args_summary={"symbol": "X"}),
            _entry(tool="read_slice", ts=_ts(10),
                   args_summary={"file": "pkg/x.py"}),
        ]
        self.assertEqual(detect_wasteful_pairs(entries), [])


class HotRereadsTests(unittest.TestCase):
    def test_threshold_repeats(self) -> None:
        entries = [
            _entry(tool="find_symbol", args_summary={"name": "X"})
            for _ in range(4)
        ]
        findings = detect_hot_rereads(entries)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["count"], 4)
        self.assertEqual(findings[0]["args_summary"], {"name": "X"})

    def test_below_threshold_silent(self) -> None:
        entries = [
            _entry(tool="find_symbol", args_summary={"name": "X"})
            for _ in range(2)
        ]
        self.assertEqual(detect_hot_rereads(entries, threshold=3), [])

    def test_argless_calls_skipped(self) -> None:
        # list_roles legitimately recurs; without args we don't flag it.
        entries = [_entry(tool="list_roles") for _ in range(10)]
        self.assertEqual(detect_hot_rereads(entries), [])

    def test_distinct_symbols_separate_buckets(self) -> None:
        entries = (
            [_entry(tool="find_symbol", args_summary={"name": "X"})] * 4
            + [_entry(tool="find_symbol", args_summary={"name": "Y"})] * 2
        )
        findings = detect_hot_rereads(entries)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["args_summary"], {"name": "X"})


class EmptyStreaksTests(unittest.TestCase):
    def test_streak_triggers(self) -> None:
        entries = [
            _entry(tool="find_call_sites", empty=True) for _ in range(3)
        ]
        findings = detect_empty_streaks(entries)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["count"], 3)

    def test_streak_resets_on_non_empty(self) -> None:
        entries = [
            _entry(tool="find_call_sites", empty=True),
            _entry(tool="find_call_sites", empty=True),
            _entry(tool="find_call_sites", empty=False),
            _entry(tool="find_call_sites", empty=True),
        ]
        self.assertEqual(detect_empty_streaks(entries, threshold=3), [])

    def test_streak_resets_on_different_tool(self) -> None:
        entries = [
            _entry(tool="find_call_sites", empty=True),
            _entry(tool="who_calls", empty=True),
            _entry(tool="find_call_sites", empty=True),
        ]
        self.assertEqual(detect_empty_streaks(entries, threshold=3), [])


class QualityReportTests(unittest.TestCase):
    def test_combines_all_three(self) -> None:
        entries = (
            # Wasteful pair
            [
                _entry(tool="find_symbol", ts=_ts(0),
                       args_keys=["name"], args_summary={"name": "X"}),
                _entry(tool="read_slice", ts=_ts(5),
                       args_summary={"file": "pkg/x.py"}),
            ]
            # Hot reread (4 same calls)
            + [
                _entry(tool="get_callees", args_summary={"symbol": "Y"})
                for _ in range(4)
            ]
            # Empty streak (3 in a row)
            + [
                _entry(tool="find_in_templates",
                       args_summary={"pattern": "missing"}, empty=True)
                for _ in range(3)
            ]
        )
        report = quality_report(entries)
        self.assertEqual(len(report["wasteful_pairs"]), 1)
        # Both `get_callees(Y) × 4` AND
        # `find_in_templates(pattern=missing) × 3` count as hot rereads.
        # The empty streak overlap is intentional — same fact-pattern,
        # different evidence (count vs run-length).
        self.assertEqual(len(report["hot_rereads"]), 2)
        self.assertEqual(len(report["empty_streaks"]), 1)
        self.assertEqual(report["total_findings"], 4)

    def test_clean_session_reports_zero(self) -> None:
        report = quality_report([
            _entry(tool="find_symbol", args_summary={"name": "Z"}),
            _entry(tool="who_calls", args_summary={"symbol": "Z"}),
        ])
        self.assertEqual(report["total_findings"], 0)


class IntegrationTests(unittest.TestCase):
    """End-to-end: dispatcher records, then engine surfaces findings."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-q-root-")
        self.metrics_dir = tempfile.mkdtemp(prefix="vc-q-metrics-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.metrics_dir, True)

        os.makedirs(os.path.join(self.root, "pkg"), exist_ok=True)
        with open(os.path.join(self.root, "agent_root.json"), "w") as fh:
            fh.write(json.dumps({
                "project_root": self.root, "modules": ["./pkg"], "roles": {},
            }))
        with open(os.path.join(self.root, "agent_symbols.json"), "w") as fh:
            fh.write(json.dumps({
                "Foo": {"file": "pkg/foo.py", "line": 1, "kind": "class"},
            }))
        with open(os.path.join(self.root, "pkg/foo.py"), "w") as fh:
            fh.write("class Foo:\n    pass\n")

        os.environ["VC_CONTEXT_METRICS_DIR"] = self.metrics_dir
        self.addCleanup(os.environ.pop, "VC_CONTEXT_METRICS_DIR", None)

        self.engine = QueryEngine(self.root)
        writer = MetricsWriter(self.root, base_dir=self.metrics_dir)
        self.dispatcher = Dispatcher(self.engine, metrics_writer=writer)

    def test_hot_reread_surfaced(self) -> None:
        for _ in range(4):
            self.dispatcher.call("find_symbol", {"name": "Foo"})
        out = self.engine.get_session_metrics(since="1h", quality=True)
        self.assertEqual(out["calls"], 4)
        rereads = out["quality"]["hot_rereads"]
        self.assertEqual(len(rereads), 1)
        self.assertEqual(rereads[0]["count"], 4)

    def test_no_quality_block_without_flag(self) -> None:
        self.dispatcher.call("find_symbol", {"name": "Foo"})
        out = self.engine.get_session_metrics(since="1h")
        self.assertNotIn("quality", out)


if __name__ == "__main__":
    unittest.main()
