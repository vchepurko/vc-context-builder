"""Unit tests for ``run_check`` caching keyed on git state."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

from query_engine import QueryEngine


def _git(cmd: list, cwd: str) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


class RunCheckCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-runcheck-")
        _git(["git", "init", "-q"], self.root)
        _git(["git", "config", "user.email", "t@t.dev"], self.root)
        _git(["git", "config", "user.name", "t"], self.root)
        with open(os.path.join(self.root, "seed.txt"), "w") as fh:
            fh.write("seed\n")
        _git(["git", "add", "."], self.root)
        _git(["git", "commit", "-qm", "seed"], self.root)
        self.engine = QueryEngine(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _stub(self, returncode: int = 0) -> mock._patch:
        """Patch ``checks.run_check`` to track invocations and return
        a stable payload."""
        result = {
            "name": "test-unit",
            "command": "pytest",
            "returncode": returncode,
            "duration_ms": 1234,
            "stdout_tail": "ok",
            "stderr_tail": "",
            "summary": "1 passed",
        }
        return mock.patch("checks.run_check", return_value=result)

    def test_second_call_returns_cached(self) -> None:
        with self._stub() as run:
            first = self.engine.run_check("test-unit")
            second = self.engine.run_check("test-unit")
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("cached", first)  # First call is fresh.
        self.assertTrue(second.get("cached"))
        self.assertEqual(second["returncode"], 0)
        self.assertEqual(second["summary"], "1 passed")

    def test_git_change_invalidates_cache(self) -> None:
        with self._stub() as run:
            self.engine.run_check("test-unit")
            # Edit a tracked file → git status --porcelain changes → fresh run.
            with open(os.path.join(self.root, "seed.txt"), "a") as fh:
                fh.write("more\n")
            self.engine.run_check("test-unit")
        self.assertEqual(run.call_count, 2)

    def test_nocache_bypasses(self) -> None:
        with self._stub() as run:
            self.engine.run_check("test-unit")
            self.engine.run_check("test-unit", nocache=True)
        self.assertEqual(run.call_count, 2)

    def test_spawn_failure_not_cached(self) -> None:
        """A returncode -3 (spawn failure) is *not* memoised — retry
        once env is fixed should reach the runner again."""
        with self._stub(returncode=-3) as run:
            self.engine.run_check("test-unit")
            self.engine.run_check("test-unit")
        self.assertEqual(run.call_count, 2)

    def test_non_git_project_skips_cache(self) -> None:
        """When project_root isn't a git repo, caching is bypassed
        entirely (no key to safely deduplicate against)."""
        non_git = tempfile.mkdtemp(prefix="vc-nogit-")
        try:
            engine = QueryEngine(non_git)
            with self._stub() as run:
                engine.run_check("test-unit")
                engine.run_check("test-unit")
            self.assertEqual(run.call_count, 2)
        finally:
            shutil.rmtree(non_git, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
