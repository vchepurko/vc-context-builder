"""Tests for the whitelisted check runner (Feature J)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from checks import list_checks, load_checks, run_check
from mcp_server import _tool_specs
from query_engine import QueryEngine


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
        fx = _Fixture(
            {
                "checks": {
                    "test": ["python", "-m", "pytest", "-q"],
                    "targeted": {
                        "cmd": ["python", "-m", "pytest"],
                        "args_policy": {"allow_paths": True, "path_roots": ["tests"]},
                    },
                    "broken": "python -m pytest",  # str → ignored
                    "empty": [],  # empty → ignored
                    "non-string-token": ["sh", 1],  # bad token → ignored
                }
            }
        )
        try:
            checks = load_checks(fx.root)
            self.assertEqual(set(checks), {"test", "targeted"})
            self.assertEqual(checks["test"], ["python", "-m", "pytest", "-q"])
            self.assertEqual(checks["targeted"], ["python", "-m", "pytest"])
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
        fx = _Fixture({"checks": {"echo": ["python3", "-c", "print('hello')"]}})
        try:
            out = run_check(fx.root, "echo")
            self.assertEqual(out["returncode"], 0)
            self.assertIn("hello", out["stdout_tail"])
            self.assertGreaterEqual(out["duration_ms"], 0)
        finally:
            fx.cleanup()

    def test_failing_command_returns_nonzero(self) -> None:
        fx = _Fixture({"checks": {"fail": ["python3", "-c", "import sys; sys.exit(7)"]}})
        try:
            out = run_check(fx.root, "fail")
            self.assertEqual(out["returncode"], 7)
        finally:
            fx.cleanup()

    def test_timeout_returns_minus_one(self) -> None:
        fx = _Fixture({"checks": {"hang": ["python3", "-c", "import time; time.sleep(5)"]}})
        try:
            out = run_check(fx.root, "hang", timeout_sec=1)
            self.assertEqual(out["returncode"], -1)
            self.assertIn("timeout", out["error"])
        finally:
            fx.cleanup()

    def test_pytest_summary_extracted(self) -> None:
        # Simulate pytest's typical "X passed in Y.Zs" tail line.
        fx = _Fixture(
            {
                "checks": {
                    "fake-pytest": [
                        "python3",
                        "-c",
                        "print('....\\n95 passed in 7.50s')",
                    ],
                }
            }
        )
        try:
            out = run_check(fx.root, "fake-pytest")
            self.assertEqual(out["returncode"], 0)
            self.assertEqual(out["summary"], "95 passed in 7.50s")
        finally:
            fx.cleanup()

    def test_spawn_failure_when_command_missing(self) -> None:
        fx = _Fixture({"checks": {"missing": ["__definitely_not_a_command__"]}})
        try:
            out = run_check(fx.root, "missing")
            self.assertEqual(out["returncode"], -3)
            self.assertIn("error", out)
        finally:
            fx.cleanup()

    def test_fixed_check_refuses_extra_args(self) -> None:
        fx = _Fixture({"checks": {"echo": ["python3", "-c", "print('ok')"]}})
        try:
            out = run_check(fx.root, "echo", args=["tests/test_one.py"])
            self.assertEqual(out["returncode"], -4)
            self.assertIn("extra args refused", out["error"])
        finally:
            fx.cleanup()

    def test_policy_allows_paths_under_roots(self) -> None:
        fx = _Fixture(
            {
                "checks": {
                    "echo": {
                        "cmd": ["python3", "-c", "import sys; print('|'.join(sys.argv[1:]))"],
                        "args_policy": {
                            "allow_paths": True,
                            "path_roots": ["tests"],
                            "allow_flags": ["-q"],
                        },
                    }
                }
            }
        )
        try:
            os.makedirs(os.path.join(fx.root, "tests"), exist_ok=True)
            out = run_check(fx.root, "echo", args=["-q", "tests/test_one.py"])
            self.assertEqual(out["returncode"], 0)
            self.assertIn("-q|tests/test_one.py", out["stdout_tail"])
        finally:
            fx.cleanup()

    def test_policy_rejects_paths_outside_roots(self) -> None:
        fx = _Fixture(
            {
                "checks": {
                    "echo": {
                        "cmd": ["python3", "-c", "print('should not run')"],
                        "args_policy": {"allow_paths": True, "path_roots": ["tests"]},
                    }
                }
            }
        )
        try:
            out = run_check(fx.root, "echo", args=["../secrets.txt"])
            self.assertEqual(out["returncode"], -4)
            self.assertIn("not allowed", out["error"])
        finally:
            fx.cleanup()

    def test_policy_allows_flag_values(self) -> None:
        fx = _Fixture(
            {
                "checks": {
                    "echo": {
                        "cmd": ["python3", "-c", "import sys; print('|'.join(sys.argv[1:]))"],
                        "args_policy": {"allow_flag_values": ["-k", "--maxfail"]},
                    }
                }
            }
        )
        try:
            out = run_check(fx.root, "echo", args=["-k", "locales", "--maxfail=1"])
            self.assertEqual(out["returncode"], 0)
            self.assertIn("-k|locales|--maxfail=1", out["stdout_tail"])
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
        self.assertIn("run_checks", names)

    def test_run_checks_requires_names(self) -> None:
        spec = next(s for s in _tool_specs() if s["name"] == "run_checks")
        self.assertEqual(spec["inputSchema"].get("required", []), ["names"])
        props = spec["inputSchema"].get("properties", {})
        self.assertIn("names", props)
        self.assertIn("timeout_sec", props)
        self.assertIn("nocache", props)

    def test_list_checks_takes_no_args(self) -> None:
        spec = next(s for s in _tool_specs() if s["name"] == "list_checks")
        self.assertEqual(spec["inputSchema"].get("required", []), [])


class TestKarmaJasmineParser(unittest.TestCase):
    SAMPLE = (
        "\x1b[32m  CollectionGateService — loadGateState()\x1b[39m\n"
        "    \x1b[31mFAILED: \x1b[39m\x1b[31mreturns 'none' when no gates\x1b[39m\n"
        "\tTypeError: this.http.get is not a function\n"
        "\t    at new CollectionGateService (commons.js:1:1)\n"
        "  StatusDotComponent\n"
        "    iconClass\n"
        "      FAILED: returns passed modifier for status 2\n"
        "\tExpected undefined to contain 'x'.\n"
        "ERROR in src/app/foo.spec.ts:12:3 - error TS2741: Property 'x' is missing.\n"
        "Executed 3 of 120 (2 FAILED)\n"
    )

    def test_extracts_failures_with_nearest_suite(self) -> None:
        from checks import _parse_karma_jasmine

        summary = _parse_karma_jasmine(self.SAMPLE, "")
        self.assertEqual(summary["framework"], "karma-jasmine")
        self.assertEqual(summary["failed"], 2)
        self.assertEqual(summary["executed"], 3)
        self.assertEqual(summary["total"], 120)
        self.assertEqual(
            summary["failures"][0],
            {
                "suite": "CollectionGateService — loadGateState()",
                "test": "returns 'none' when no gates",
            },
        )
        # Nearest describe header wins (the leaf "iconClass", not the parent component).
        self.assertEqual(summary["failures"][1]["suite"], "iconClass")
        self.assertEqual(summary["failures"][1]["test"], "returns passed modifier for status 2")

    def test_captures_compile_errors(self) -> None:
        from checks import _parse_karma_jasmine

        summary = _parse_karma_jasmine(self.SAMPLE, "")
        self.assertEqual(len(summary["compileErrors"]), 1)
        self.assertIn("src/app/foo.spec.ts:12:3", summary["compileErrors"][0])

    def test_clean_run_has_no_failures_or_compile_errors(self) -> None:
        from checks import _parse_karma_jasmine

        summary = _parse_karma_jasmine("Executed 120 of 120 SUCCESS\n", "")
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["failures"], [])
        self.assertEqual(summary["total"], 120)
        self.assertNotIn("compileErrors", summary)

    def test_parser_field_carried_in_spec(self) -> None:
        fx = _Fixture(
            {
                "checks": {
                    "ng-test": {
                        "cmd": ["npm", "run", "test"],
                        "parser": "karma-jasmine",
                    }
                }
            }
        )
        try:
            from checks import load_check_specs

            specs = load_check_specs(fx.root)
            self.assertEqual(specs["ng-test"]["parser"], "karma-jasmine")
        finally:
            fx.cleanup()


if __name__ == "__main__":
    unittest.main()
