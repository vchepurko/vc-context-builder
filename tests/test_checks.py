"""Tests for the whitelisted check runner (Feature J)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from checks import list_checks, load_checks, run_check  # noqa: E402
from mcp_server import _tool_specs  # noqa: E402
from query_engine import QueryEngine  # noqa: E402


def _write_config(root: str, payload: dict) -> None:
    path = os.path.join(root, ".vc-context")
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "conventions.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class _Fixture:
    def __init__(self, payload: dict) -> None:
        self.root = tempfile.mkdtemp(prefix="checks_")
        _write_config(self.root, payload)

    def cleanup(self) -> None:
        for cur, dirs, files in os.walk(self.root, topdown=False):
            for f in files:
                os.remove(os.path.join(cur, f))
            for d in dirs:
                os.rmdir(os.path.join(cur, d))
        os.rmdir(self.root)


class TestLoadChecks(unittest.TestCase):
    def test_returns_argv_lists_only(self) -> None:
        fx = _Fixture({
            "checks": {
                "test": ["python", "-m", "pytest", "-q"],
                "broken": "python -m pytest",            # str → ignored
                "empty": [],                             # empty → ignored
                "non-string-token": ["sh", 1],           # bad token → ignored
            }
        })
        try:
            checks = load_checks(fx.root)
            self.assertEqual(set(checks), {"test"})
            self.assertEqual(checks["test"], ["python", "-m", "pytest", "-q"])
        finally:
            fx.cleanup()

    def test_missing_block_returns_empty(self) -> None:
        fx = _Fixture({"rules": []})
        try:
            self.assertEqual(load_checks(fx.root), {})
            self.assertEqual(list_checks(fx.root), [])
        finally:
            fx.cleanup()


class TestRunCheck(unittest.TestCase):
    def test_unknown_name_refused_with_negative_returncode(self) -> None:
        fx = _Fixture({"checks": {"test": ["true"]}})
        try:
            out = run_check(fx.root, "nope")
            self.assertEqual(out["returncode"], -2)
            self.assertIn("error", out)
            self.assertEqual(out["command"], [])
        finally:
            fx.cleanup()

    def test_runs_simple_command_and_captures_output(self) -> None:
        fx = _Fixture({
            "checks": {"echo": ["python3", "-c", "print('hello')"]}
        })
        try:
            out = run_check(fx.root, "echo")
            self.assertEqual(out["returncode"], 0)
            self.assertIn("hello", out["stdout_tail"])
            self.assertGreaterEqual(out["duration_ms"], 0)
        finally:
            fx.cleanup()

    def test_failing_command_returns_nonzero(self) -> None:
        fx = _Fixture({
            "checks": {"fail": ["python3", "-c", "import sys; sys.exit(7)"]}
        })
        try:
            out = run_check(fx.root, "fail")
            self.assertEqual(out["returncode"], 7)
        finally:
            fx.cleanup()

    def test_timeout_returns_minus_one(self) -> None:
        fx = _Fixture({
            "checks": {"hang": ["python3", "-c", "import time; time.sleep(5)"]}
        })
        try:
            out = run_check(fx.root, "hang", timeout_sec=1)
            self.assertEqual(out["returncode"], -1)
            self.assertIn("timeout", out["error"])
        finally:
            fx.cleanup()

    def test_pytest_summary_extracted(self) -> None:
        # Simulate pytest's typical "X passed in Y.Zs" tail line.
        fx = _Fixture({
            "checks": {
                "fake-pytest": [
                    "python3", "-c",
                    "print('....\\n95 passed in 7.50s')",
                ],
            }
        })
        try:
            out = run_check(fx.root, "fake-pytest")
            self.assertEqual(out["returncode"], 0)
            self.assertEqual(out["summary"], "95 passed in 7.50s")
        finally:
            fx.cleanup()

    def test_spawn_failure_when_command_missing(self) -> None:
        fx = _Fixture({
            "checks": {"missing": ["__definitely_not_a_command__"]}
        })
        try:
            out = run_check(fx.root, "missing")
            self.assertEqual(out["returncode"], -3)
            self.assertIn("error", out)
        finally:
            fx.cleanup()


class TestQueryEngineWiring(unittest.TestCase):
    def test_round_trip_via_engine(self) -> None:
        fx = _Fixture({"checks": {"echo": ["python3", "-c", "print('ok')"]}})
        try:
            engine = QueryEngine(fx.root)
            self.assertEqual(engine.list_checks(), ["echo"])
            out = engine.run_check("echo")
            self.assertEqual(out["returncode"], 0)
            self.assertIn("ok", out["stdout_tail"])
        finally:
            fx.cleanup()


class TestMcpToolWiring(unittest.TestCase):
    def test_tools_registered(self) -> None:
        names = {spec["name"] for spec in _tool_specs()}
        self.assertIn("list_checks", names)
        self.assertIn("run_check", names)

    def test_run_check_requires_name(self) -> None:
        spec = next(s for s in _tool_specs() if s["name"] == "run_check")
        self.assertEqual(spec["inputSchema"].get("required", []), ["name"])

    def test_list_checks_takes_no_args(self) -> None:
        spec = next(s for s in _tool_specs() if s["name"] == "list_checks")
        self.assertEqual(spec["inputSchema"].get("required", []), [])


if __name__ == "__main__":
    unittest.main()
