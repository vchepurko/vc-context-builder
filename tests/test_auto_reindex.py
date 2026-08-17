"""Tests for optional MCP-startup auto-reindex."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import auto_reindex


def _write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class AutoReindexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-auto-reindex-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _configure(self, *, enabled: bool = True, interval_seconds: int = 60) -> None:
        _write_json(
            os.path.join(self.root, ".vc-context", "conventions.json"),
            {
                "auto_reindex": {
                    "enabled": enabled,
                    "interval_seconds": interval_seconds,
                }
            },
        )

    def _write_agent_root(self, mtime: float) -> None:
        path = os.path.join(self.root, ".vc-context", "index", "agent_root.json")
        _write_json(path, {"modules": []})
        os.utime(path, (mtime, mtime))

    def test_disabled_by_default(self) -> None:
        self.assertFalse(auto_reindex.should_auto_reindex(self.root, now=time.time()))

    def test_default_interval_is_thirty_minutes(self) -> None:
        self.assertEqual(auto_reindex._interval_seconds({}), 1800)

    def test_enabled_missing_index_runs(self) -> None:
        self._configure()
        self.assertTrue(auto_reindex.should_auto_reindex(self.root, now=time.time()))

    def test_enabled_fresh_index_skips(self) -> None:
        now = time.time()
        self._configure(interval_seconds=3600)
        self._write_agent_root(now - 10)
        self.assertFalse(auto_reindex.should_auto_reindex(self.root, now=now))

    def test_enabled_stale_index_runs(self) -> None:
        now = time.time()
        self._configure(interval_seconds=60)
        self._write_agent_root(now - 120)
        self.assertTrue(auto_reindex.should_auto_reindex(self.root, now=now))

    def test_maybe_auto_reindex_spawns_builder_when_stale(self) -> None:
        self._configure()
        with mock.patch("auto_reindex.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stderr = ""
            out = auto_reindex.maybe_auto_reindex(self.root)
        self.assertTrue(out["ok"])
        self.assertTrue(out["ran"])
        args = run.call_args.args[0]
        self.assertIn("agent_map.py", args[1])
        self.assertEqual(args[-2:], ["--root", self.root])


if __name__ == "__main__":
    unittest.main()
