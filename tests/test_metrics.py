"""Tests for the per-call telemetry sidecar.

Covered:

* :class:`MetricsWriter` emits one JSONL line per ``record()`` call,
  with the right shape and the right ``empty`` heuristic.
* ``read_metrics`` filters by project hash and ``since`` window.
* :func:`aggregate` produces the expected summary keys.
* ``Dispatcher.call`` calls into the writer with the right payload
  and survives a writer that raises (fail-open contract).
* ``QueryEngine.get_session_metrics`` honours ``since`` / ``group_by``.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from mcp.dispatcher import Dispatcher
from mcp.metrics import (
    MetricsWriter,
    _is_empty,
    _parse_since,
    aggregate,
    read_metrics,
)
from query_engine import QueryEngine


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _make_root() -> str:
    tmp = tempfile.mkdtemp(prefix="vc-metrics-")
    _write(
        os.path.join(tmp, "agent_root.json"),
        json.dumps(
            {
                "project_root": tmp,
                "modules": ["./pkg"],
                "roles": {},
            }
        ),
    )
    _write(
        os.path.join(tmp, "agent_symbols.json"),
        json.dumps(
            {
                "foo": {"file": "pkg/a.py", "line": 1, "kind": "func"},
            }
        ),
    )
    return tmp


class IsEmptyTests(unittest.TestCase):
    def test_none(self) -> None:
        self.assertTrue(_is_empty(None))

    def test_empty_containers(self) -> None:
        self.assertTrue(_is_empty([]))
        self.assertTrue(_is_empty({}))
        self.assertTrue(_is_empty(""))

    def test_non_empty(self) -> None:
        self.assertFalse(_is_empty([1]))
        self.assertFalse(_is_empty({"a": 1}))
        self.assertFalse(_is_empty("hello"))

    def test_total_zero_summary(self) -> None:
        # Violations tools return {"total": 0, ...} when nothing matched.
        self.assertTrue(_is_empty({"total": 0, "by_code": {}}))


class ParseSinceTests(unittest.TestCase):
    def test_none_means_no_filter(self) -> None:
        self.assertIsNone(_parse_since(None))
        self.assertIsNone(_parse_since(""))
        self.assertIsNone(_parse_since("all"))

    def test_units(self) -> None:
        for spec in ("1h", "24H", "7d", "30m"):
            self.assertIsNotNone(_parse_since(spec))

    def test_today(self) -> None:
        out = _parse_since("today")
        assert out is not None
        self.assertEqual((out.hour, out.minute, out.second), (0, 0, 0))

    def test_garbage(self) -> None:
        self.assertIsNone(_parse_since("yesterday"))
        self.assertIsNone(_parse_since("3xq"))


class WriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-w-root-")
        self.metrics_dir = tempfile.mkdtemp(prefix="vc-w-metrics-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.metrics_dir, True)
        self.writer = MetricsWriter(self.root, base_dir=self.metrics_dir)

    def _entries(self) -> list:
        return read_metrics(self.root, base_dir=self.metrics_dir)

    def test_record_emits_one_line(self) -> None:
        self.writer.record("find_symbol", {"name": "foo"}, {"file": "x.py"}, 7, True)
        entries = self._entries()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["tool"], "find_symbol")
        self.assertEqual(e["args_keys"], ["name"])
        self.assertEqual(e["t_ms"], 7)
        self.assertTrue(e["ok"])
        self.assertFalse(e["empty"])
        self.assertGreater(e["result_bytes"], 0)
        self.assertGreater(e["approx_tokens"], 0)

    def test_record_empty_marks_empty(self) -> None:
        self.writer.record("who_calls", {"symbol": "ghost"}, [], 1, True)
        entries = self._entries()
        self.assertTrue(entries[0]["empty"])

    def test_record_handles_unjsonable_result(self) -> None:
        # Set / function / lambda — non-default json types. ``default=str``
        # in metrics.py keeps the writer from crashing.
        self.writer.record("weird", {}, {1, 2, 3}, 1, True)
        self.assertEqual(len(self._entries()), 1)


class ReadMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-r-root-")
        self.metrics_dir = tempfile.mkdtemp(prefix="vc-r-metrics-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.metrics_dir, True)

    def _hand_emit(self, ts: str, tool: str = "x") -> None:
        # Use the real filename helper so reads work the same way.
        from mcp.metrics import _today_filename

        # Force date in filename to match the date prefix in `ts`.
        # _today_filename uses today's date — for explicit ts we just
        # write into the same file (read_metrics doesn't filter by
        # filename date).
        path = _today_filename(self.root, self.metrics_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": ts,
                        "tool": tool,
                        "args_keys": [],
                        "result_bytes": 10,
                        "approx_tokens": 2,
                        "t_ms": 1,
                        "ok": True,
                        "empty": False,
                    }
                )
                + "\n"
            )

    def test_filters_by_repo_hash(self) -> None:
        # An entry for a *different* project shouldn't surface.
        other = tempfile.mkdtemp(prefix="vc-r-other-")
        self.addCleanup(shutil.rmtree, other, True)
        MetricsWriter(other, base_dir=self.metrics_dir).record(
            "x",
            {},
            "y",
            1,
            True,
        )
        self.assertEqual(read_metrics(self.root, base_dir=self.metrics_dir), [])

    def test_since_window(self) -> None:
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=2)).isoformat(
            timespec="seconds"
        )
        new = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        self._hand_emit(old, "old")
        self._hand_emit(new, "new")

        all_entries = read_metrics(self.root, base_dir=self.metrics_dir)
        self.assertEqual(len(all_entries), 2)

        recent = read_metrics(
            self.root,
            since="1h",
            base_dir=self.metrics_dir,
        )
        self.assertEqual([e["tool"] for e in recent], ["new"])


class AggregateTests(unittest.TestCase):
    def _entry(self, **kw) -> dict:
        base = {
            "ts": "2026-05-07T10:00:00+00:00",
            "tool": "find_symbol",
            "args_keys": [],
            "result_bytes": 100,
            "approx_tokens": 25,
            "t_ms": 5,
            "ok": True,
            "empty": False,
        }
        base.update(kw)
        return base

    def test_empty_input(self) -> None:
        out = aggregate([])
        self.assertEqual(out["calls"], 0)
        self.assertEqual(out["by_tool"], {})

    def test_by_tool(self) -> None:
        out = aggregate(
            [
                self._entry(tool="find_symbol", t_ms=10),
                self._entry(tool="find_symbol", t_ms=20, empty=True),
                self._entry(tool="who_calls", t_ms=5),
            ]
        )
        self.assertEqual(out["calls"], 3)
        self.assertEqual(out["empty_ratio"], round(1 / 3, 3))
        self.assertEqual(out["by_tool"]["find_symbol"]["calls"], 2)
        self.assertEqual(out["by_tool"]["find_symbol"]["empty_ratio"], 0.5)
        self.assertEqual(out["by_tool"]["who_calls"]["calls"], 1)

    def test_by_hour(self) -> None:
        out = aggregate(
            [
                self._entry(ts="2026-05-07T10:00:00+00:00"),
                self._entry(ts="2026-05-07T10:30:00+00:00"),
                self._entry(ts="2026-05-07T11:15:00+00:00"),
            ],
            group_by="hour",
        )
        self.assertIn("2026-05-07T10", out["by_hour"])
        self.assertEqual(out["by_hour"]["2026-05-07T10"]["calls"], 2)


class DispatcherIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _make_root()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.metrics_dir = tempfile.mkdtemp(prefix="vc-d-metrics-")
        self.addCleanup(shutil.rmtree, self.metrics_dir, True)

        self.engine = QueryEngine(self.root)
        self.writer = MetricsWriter(self.root, base_dir=self.metrics_dir)
        self.dispatcher = Dispatcher(self.engine, metrics_writer=self.writer)

    def test_call_emits_metric(self) -> None:
        out = self.dispatcher.call("find_symbol", {"name": "foo"})
        self.assertIsNotNone(out)
        entries = read_metrics(self.root, base_dir=self.metrics_dir)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["tool"], "find_symbol")
        self.assertTrue(entries[0]["ok"])

    def test_call_emits_on_unknown_tool(self) -> None:
        # Should still record (with ok=False) but raise.
        with self.assertRaises(ValueError):
            self.dispatcher.call("nonexistent_tool", {})
        entries = read_metrics(self.root, base_dir=self.metrics_dir)
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]["ok"])

    def test_writer_failure_does_not_break_call(self) -> None:
        # Replace the writer's record() with a bomb; the dispatcher
        # must still return the call's result.
        def boom(*_a, **_k) -> None:
            raise RuntimeError("disk full")

        self.writer.record = boom  # type: ignore[assignment]
        out = self.dispatcher.call("find_symbol", {"name": "foo"})
        self.assertIsNotNone(out)

    def test_no_writer_means_no_metric_path(self) -> None:
        # When constructed without a writer, no JSONL is created.
        d = Dispatcher(self.engine)
        d.call("find_symbol", {"name": "foo"})
        self.assertEqual(read_metrics(self.root, base_dir=self.metrics_dir), [])


class GetSessionMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _make_root()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.metrics_dir = tempfile.mkdtemp(prefix="vc-g-metrics-")
        self.addCleanup(shutil.rmtree, self.metrics_dir, True)
        self._prev_env = os.environ.get("VC_CONTEXT_METRICS_DIR")
        os.environ["VC_CONTEXT_METRICS_DIR"] = self.metrics_dir
        self.addCleanup(self._restore_env)

        self.engine = QueryEngine(self.root)
        writer = MetricsWriter(self.root, base_dir=self.metrics_dir)
        d = Dispatcher(self.engine, metrics_writer=writer)
        d.call("find_symbol", {"name": "foo"})
        d.call("find_symbol", {"name": "ghost"})  # → None → empty
        # tiny sleep so timestamps differ when granular checks matter
        time.sleep(0.001)

    def _restore_env(self) -> None:
        if self._prev_env is None:
            os.environ.pop("VC_CONTEXT_METRICS_DIR", None)
        else:
            os.environ["VC_CONTEXT_METRICS_DIR"] = self._prev_env

    def test_summary_shape(self) -> None:
        out = self.engine.get_session_metrics(since="1h")
        self.assertEqual(out["calls"], 2)
        self.assertIn("by_tool", out)
        self.assertEqual(out["by_tool"]["find_symbol"]["calls"], 2)
        self.assertEqual(out["empty_ratio"], 0.5)

    def test_group_by_empty(self) -> None:
        out = self.engine.get_session_metrics(since="1h", group_by="empty")
        self.assertIn("empty", out["by_empty"])
        self.assertIn("non-empty", out["by_empty"])


if __name__ == "__main__":
    unittest.main()
