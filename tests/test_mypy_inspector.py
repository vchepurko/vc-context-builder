"""Tests for the mypy output collector.

The collector wraps a subprocess call (mypy --output=json), parses
JSON-per-line records, and projects them to the slim shape MCP
clients consume. Subprocess execution itself is integration-level —
unit tests focus on the pure-data helpers (`_norm_file`, `_to_entry`)
and the filtering / aggregation in `collect()` (via a stubbed
`run_mypy`).
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import linters.mypy_inspector as mypy_inspector
from linters.mypy_inspector import _norm_file, _to_entry, collect


class NormFileTests(unittest.TestCase):
    def test_strips_project_root_prefix(self) -> None:
        # On any OS, the helper should normalise an absolute path
        # under project_root to a project-relative POSIX form.
        root = "/tmp/proj"
        self.assertEqual(_norm_file("/tmp/proj/src/x.py", root), "src/x.py")

    def test_already_relative_path_passes_through(self) -> None:
        self.assertEqual(_norm_file("src/x.py", "/tmp/proj"), "src/x.py")


class ToEntryTests(unittest.TestCase):
    def test_compresses_to_required_fields(self) -> None:
        raw = {
            "file": "/tmp/proj/x.py",
            "line": 12,
            "end_line": 12,
            "column": 4,
            "end_column": 8,
            "hint": "ignore me",
            "code": "union-attr",
            "severity": "error",
            "message": "boom",
        }
        out = _to_entry(raw, "/tmp/proj")
        self.assertEqual(
            out,
            {
                "file": "x.py",
                "line": 12,
                "end_line": 12,
                "code": "union-attr",
                "severity": "error",
                "message": "boom",
            },
        )

    def test_handles_missing_keys(self) -> None:
        out = _to_entry({}, "/tmp/proj")
        self.assertEqual(out["line"], 0)
        self.assertEqual(out["code"], "")
        self.assertEqual(out["message"], "")


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        # All collect() tests assume mypy IS enabled. Skip-detection
        # is its own surface and isn't worth re-mocking here.
        self.should_skip = mock.patch.object(
            mypy_inspector,
            "should_skip_mypy",
            return_value=(False, ""),
        )
        self.should_skip.start()
        self.addCleanup(self.should_skip.stop)

        self._fake_records = [
            {
                "file": "/r/a.py",
                "line": 1,
                "code": "union-attr",
                "severity": "error",
                "message": "X",
            },
            {
                "file": "/r/a.py",
                "line": 5,
                "code": "union-attr",
                "severity": "error",
                "message": "Y",
            },
            {"file": "/r/b.py", "line": 1, "code": "arg-type", "severity": "error", "message": "Z"},
        ]
        self.run_mypy = mock.patch.object(
            mypy_inspector,
            "run_mypy",
            return_value=self._fake_records,
        )
        self.run_mypy.start()
        self.addCleanup(self.run_mypy.stop)

    def test_aggregates_by_code_and_file(self) -> None:
        out = collect("/r")
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["by_code"], {"union-attr": 2, "arg-type": 1})
        self.assertEqual(out["by_file"], {"a.py": 2, "b.py": 1})
        self.assertEqual(len(out["violations"]), 3)

    def test_filter_by_code(self) -> None:
        out = collect("/r", code="arg-type")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["violations"][0]["file"], "b.py")

    def test_filter_by_path_prefix(self) -> None:
        out = collect("/r", path_prefix="a.")
        self.assertEqual(out["total"], 2)

    def test_summary_drops_violations(self) -> None:
        out = collect("/r", summary=True)
        self.assertNotIn("violations", out)
        self.assertEqual(out["total"], 3)

    def test_skip_short_circuit(self) -> None:
        # When skip detector returns True, collect() must not invoke
        # run_mypy and must surface the skip reason.
        self.should_skip.stop()
        with mock.patch.object(
            mypy_inspector,
            "should_skip_mypy",
            return_value=(True, "no python files"),
        ):
            out = collect("/r")
        self.assertTrue(out["skipped"])
        self.assertEqual(out["reason"], "no python files")
        self.assertEqual(out["total"], 0)


if __name__ == "__main__":
    unittest.main()
