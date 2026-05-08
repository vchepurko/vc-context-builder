"""Tests for the coverage_for_role MCP tool (Feature G).

Validates two modes (whole-project + scoped), legacy umbrella role
expansion, missing/covered lists, percentage rounding, and MCP wiring.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mcp_server import _tool_specs
from query_engine import QueryEngine


def _write(path: str, body: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


class _Fixture:
    def __init__(self) -> None:
        self.root = tempfile.mkdtemp(prefix="cov_role_")
        # Symbols: 4 fsm-message-handlers, 2 routes, 1 unrelated.
        symbols = {
            "h_a": {"file": "bot/h.py", "kind": "async-func", "role": "fsm-message-handler"},
            "h_b": {"file": "bot/h.py", "kind": "async-func", "role": "fsm-message-handler"},
            "h_c": {"file": "bot/h.py", "kind": "async-func", "role": "callback-handler"},
            "h_d": {"file": "bot/h.py", "kind": "async-func", "role": "command-handler"},
            "r_x": {"file": "backend/r.py", "kind": "async-func", "role": "route"},
            "r_y": {"file": "backend/r.py", "kind": "async-func", "role": "route"},
            "noop": {"file": "lib/x.py", "kind": "func"},
        }
        # Tests: only h_a and r_x have a linked test.
        tests = {
            "h_a": {"test_file": "tests/test_h.py", "test_function": "test_h_a", "line": 10},
            "r_x": {"test_file": "tests/test_r.py", "test_function": "test_r_x", "line": 20},
        }
        # Roles aggregator (mirrors agent_root.json shape).
        root = {
            "project_root": self.root,
            "modules": [],
            "roles": {
                "fsm-message-handler": ["h_a", "h_b"],
                "callback-handler": ["h_c"],
                "command-handler": ["h_d"],
                "route": ["r_x", "r_y"],
            },
        }

        _write(os.path.join(self.root, "agent_symbols.json"), json.dumps(symbols))
        _write(os.path.join(self.root, "agent_tests.json"), json.dumps(tests))
        _write(os.path.join(self.root, "agent_root.json"), json.dumps(root))

    def cleanup(self) -> None:
        for cur, dirs, files in os.walk(self.root, topdown=False):
            for f in files:
                os.remove(os.path.join(cur, f))
            for d in dirs:
                os.rmdir(os.path.join(cur, d))
        os.rmdir(self.root)


class TestProjectWide(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()
        self.engine = QueryEngine(self.fx.root)

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_no_role_returns_summary_shape(self) -> None:
        out = self.engine.coverage_for_role()
        self.assertIn("roles", out)
        self.assertIn("overall", out)
        # Each role has total + with_test + coverage_pct keys.
        for entry in out["roles"].values():
            self.assertEqual(set(entry.keys()), {"total", "with_test", "coverage_pct"})

    def test_overall_counts_every_symbol(self) -> None:
        out = self.engine.coverage_for_role()
        self.assertEqual(out["overall"]["total"], 7)
        self.assertEqual(out["overall"]["with_test"], 2)
        self.assertEqual(out["overall"]["coverage_pct"], round(2 * 100 / 7, 1))

    def test_per_role_counts(self) -> None:
        out = self.engine.coverage_for_role()
        roles = out["roles"]
        self.assertEqual(
            roles["fsm-message-handler"],
            {
                "total": 2,
                "with_test": 1,
                "coverage_pct": 50.0,
            },
        )
        self.assertEqual(
            roles["route"],
            {
                "total": 2,
                "with_test": 1,
                "coverage_pct": 50.0,
            },
        )
        self.assertEqual(
            roles["callback-handler"],
            {
                "total": 1,
                "with_test": 0,
                "coverage_pct": 0.0,
            },
        )


class TestScopedToRole(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()
        self.engine = QueryEngine(self.fx.root)

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_scoped_returns_missing_and_covered_lists(self) -> None:
        out = self.engine.coverage_for_role("fsm-message-handler")
        self.assertEqual(out["role"], "fsm-message-handler")
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["with_test"], 1)
        self.assertEqual(out["coverage_pct"], 50.0)

        missing_names = [m["name"] for m in out["missing"]]
        self.assertEqual(missing_names, ["h_b"])
        self.assertEqual(out["missing"][0]["file"], "bot/h.py")

        covered_names = [c["name"] for c in out["covered"]]
        self.assertEqual(covered_names, ["h_a"])
        self.assertEqual(out["covered"][0]["test_file"], "tests/test_h.py")
        self.assertEqual(out["covered"][0]["test_function"], "test_h_a")

    def test_legacy_umbrella_aiogram_handler_unions_subroles(self) -> None:
        # find_by_role expands "aiogram-handler" → callback + command +
        # fsm-message + text-match + catch-all + raw aiogram-handler.
        out = self.engine.coverage_for_role("aiogram-handler")
        # Pool: h_a, h_b (fsm-message), h_c (callback), h_d (command).
        self.assertEqual(out["total"], 4)
        self.assertEqual(out["with_test"], 1)
        self.assertEqual(
            sorted(m["name"] for m in out["missing"]),
            ["h_b", "h_c", "h_d"],
        )

    def test_unknown_role_returns_empty_buckets_not_error(self) -> None:
        out = self.engine.coverage_for_role("no-such-role")
        self.assertEqual(out["total"], 0)
        self.assertEqual(out["with_test"], 0)
        self.assertEqual(out["coverage_pct"], 0.0)
        self.assertEqual(out["missing"], [])
        self.assertEqual(out["covered"], [])


class TestMcpToolWiring(unittest.TestCase):
    def test_coverage_for_role_listed_in_tool_specs(self) -> None:
        names = {spec["name"] for spec in _tool_specs()}
        self.assertIn("coverage_for_role", names)

    def test_role_param_is_optional(self) -> None:
        spec = next(s for s in _tool_specs() if s["name"] == "coverage_for_role")
        # No "required" key, or empty list — caller can omit role.
        required = spec["inputSchema"].get("required", [])
        self.assertEqual(required, [])


if __name__ == "__main__":
    unittest.main()
