"""Unit tests for notify_log_reader (Feature N).

Reader-side only — the writer lives in the parent project's
services/notify/log.py and is tested separately there.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

import notify_log_reader
from query_engine import QueryEngine


def _write_jsonl(path: str, records: list) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _seed(root: str) -> str:
    """Standard test layout: current file + 2 rotated companions."""
    base = os.path.join(root, "logs", "notify.jsonl")
    _write_jsonl(
        base,
        [
            {
                "ts": 1714000000,
                "kind": "staff.added",
                "recipient_uid": 1,
                "channel": "telegram",
                "outcome": "sent",
                "keys": ["name"],
            },
            {
                "ts": 1714000010,
                "kind": "staff.added",
                "recipient_uid": 1,
                "channel": "email",
                "outcome": "failed",
                "keys": ["name"],
            },
        ],
    )
    _write_jsonl(
        base + ".2026-04-30",
        [
            {
                "ts": 1700000000,
                "kind": "order.placed.admin",
                "recipient_uid": 9,
                "channel": "telegram",
                "outcome": "sent",
                "keys": ["order_id"],
            },
        ],
    )
    _write_jsonl(
        base + ".2026-04-29",
        [
            {
                "ts": 1690000000,
                "kind": "auction.outbid",
                "recipient_uid": 7,
                "channel": "telegram",
                "outcome": "skipped",
                "keys": [],
            },
        ],
    )
    return base


class NotifyLogReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-notify-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_no_log_returns_empty(self) -> None:
        # Fresh project with no logs/ at all.
        self.assertEqual(notify_log_reader.search(self.root), [])
        s = notify_log_reader.stats(self.root)
        self.assertEqual(s, {"total": 0, "by_kind": {}, "by_channel": {}})

    def test_search_no_filters_returns_all_records_up_to_limit(self) -> None:
        _seed(self.root)
        out = notify_log_reader.search(self.root)
        self.assertEqual(len(out), 4)

    def test_search_respects_kind_filter(self) -> None:
        _seed(self.root)
        out = notify_log_reader.search(self.root, kind="staff.added")
        self.assertEqual(len(out), 2)
        for rec in out:
            self.assertEqual(rec["kind"], "staff.added")

    def test_search_combines_recipient_and_channel_filters(self) -> None:
        _seed(self.root)
        out = notify_log_reader.search(
            self.root,
            recipient=1,
            channel="email",
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["channel"], "email")
        self.assertEqual(out[0]["recipient_uid"], 1)

    def test_search_limit_caps_response_size(self) -> None:
        """MCP guard: large filters shouldn't dump megabytes of JSONL
        into the model's context window."""
        _seed(self.root)
        out = notify_log_reader.search(self.root, limit=2)
        self.assertEqual(len(out), 2)

    def test_stats_counts_by_kind_and_channel(self) -> None:
        _seed(self.root)
        s = notify_log_reader.stats(self.root)
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["by_kind"]["staff.added"]["sent"], 1)
        self.assertEqual(s["by_kind"]["staff.added"]["failed"], 1)
        self.assertEqual(s["by_channel"]["telegram"]["sent"], 2)
        self.assertEqual(s["by_channel"]["email"]["failed"], 1)

    def test_corrupted_lines_skipped(self) -> None:
        """Half-written line at the tail of a crash shouldn't bury
        the rest of the file."""
        base = os.path.join(self.root, "logs", "notify.jsonl")
        os.makedirs(os.path.dirname(base), exist_ok=True)
        with open(base, "w", encoding="utf-8") as fh:
            fh.write(
                '{"ts":1,"kind":"a","recipient_uid":1,'
                '"channel":"telegram","outcome":"sent","keys":[]}\n'
            )
            fh.write("not json\n")
            fh.write(
                '{"ts":2,"kind":"b","recipient_uid":2,'
                '"channel":"email","outcome":"failed","keys":[]}\n'
            )
        out = notify_log_reader.search(self.root)
        self.assertEqual(len(out), 2)

    def test_conventions_override_path(self) -> None:
        """Project-specific layouts override the default
        ``logs/notify.jsonl`` via .vc-context/conventions.json."""
        conv_dir = os.path.join(self.root, ".vc-context")
        os.makedirs(conv_dir, exist_ok=True)
        with open(os.path.join(conv_dir, "conventions.json"), "w") as fh:
            json.dump({"notify_log": {"path": "var/audit.jsonl"}}, fh)
        custom = os.path.join(self.root, "var", "audit.jsonl")
        _write_jsonl(
            custom,
            [
                {
                    "ts": 1,
                    "kind": "x",
                    "recipient_uid": 1,
                    "channel": "telegram",
                    "outcome": "sent",
                    "keys": [],
                }
            ],
        )
        out = notify_log_reader.search(self.root)
        self.assertEqual(len(out), 1)


class NotifyLogQueryEngineTests(unittest.TestCase):
    """Same behaviour through the QueryEngine surface — what the
    MCP server actually calls."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-notify-")
        self.addCleanup(shutil.rmtree, self.root, True)
        # QueryEngine requires agent_root.json to exist.
        with open(os.path.join(self.root, "agent_root.json"), "w") as fh:
            json.dump({"project_root": self.root, "modules": ["."], "roles": {}}, fh)

    def test_engine_search_threads_through(self) -> None:
        _seed(self.root)
        engine = QueryEngine(self.root)
        out = engine.notify_log_search(channel="telegram")
        # 3 telegram records across 3 files (1 staff.added.sent + 2 from rotated).
        self.assertEqual(len(out), 3)
        for r in out:
            self.assertEqual(r["channel"], "telegram")

    def test_engine_stats_threads_through(self) -> None:
        _seed(self.root)
        engine = QueryEngine(self.root)
        s = engine.notify_log_stats()
        self.assertEqual(s["total"], 4)
        self.assertIn("staff.added", s["by_kind"])
        self.assertIn("telegram", s["by_channel"])


if __name__ == "__main__":
    unittest.main()
