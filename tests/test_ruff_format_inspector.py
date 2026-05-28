"""Tests for the ruff-format check collector.

The inspector parses lines like ``Would reformat: path/to/file.py``
from ``ruff format --check`` stdout and projects them into the slim
shape MCP exposes. Subprocess execution is integration-level — these
unit tests cover the pure-data helpers and the filter/aggregate path
via a stubbed ``run_ruff_format``.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import linters.ruff_format_inspector as ruff_format_inspector
from linters.ruff_format_inspector import _norm_file, collect


class NormFileTests(unittest.TestCase):
    def test_strips_project_root_prefix(self) -> None:
        self.assertEqual(
            _norm_file("/tmp/proj/x.py", "/tmp/proj"),
            "x.py",
        )

    def test_passes_through_relative(self) -> None:
        self.assertEqual(
            _norm_file("src/x.py", "/tmp/proj"),
            "src/x.py",
        )


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        # Mock the skip-detector so collect() actually runs.
        # `should_skip_ruff` is imported lazily inside collect(), so
        # we patch it on the *importable* module.
        import linters.ruff_inspector as ruff_inspector

        self._patch_skip = mock.patch.object(
            ruff_inspector,
            "should_skip_ruff",
            return_value=(False, ""),
        )
        self._patch_skip.start()
        self.addCleanup(self._patch_skip.stop)

        self._files = ["a.py", "b.py", "src/c.py"]
        self._patch_run = mock.patch.object(
            ruff_format_inspector,
            "run_ruff_format",
            return_value=list(self._files),
        )
        self._patch_run.start()
        self.addCleanup(self._patch_run.stop)

    def test_returns_total_and_files(self) -> None:
        out = collect("/r")
        self.assertEqual(out["total"], 3)
        # Must be sorted alphabetically.
        self.assertEqual(out["files"], ["a.py", "b.py", "src/c.py"])

    def test_filter_by_path_prefix(self) -> None:
        out = collect("/r", path_prefix="src/")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["files"], ["src/c.py"])

    def test_summary_drops_file_list(self) -> None:
        out = collect("/r", summary=True)
        self.assertNotIn("files", out)
        self.assertEqual(out["total"], 3)

    def test_skip_short_circuits(self) -> None:
        self._patch_skip.stop()
        import linters.ruff_inspector as ruff_inspector

        with mock.patch.object(
            ruff_inspector,
            "should_skip_ruff",
            return_value=(True, "no python"),
        ):
            out = collect("/r")
        self.assertTrue(out["skipped"])
        self.assertEqual(out["reason"], "no python")
        self.assertEqual(out["total"], 0)


if __name__ == "__main__":
    unittest.main()
