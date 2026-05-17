"""Unit tests for ``find_handlers_without_tests`` — coverage gap
auditor over aiogram handlers (or any custom role)."""

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

from query_engine import QueryEngine


class FindHandlersWithoutTestsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-coverage-")
        # Seed the on-disk artefacts QueryEngine reads. We don't run
        # agent_map.py — these are minimal hand-crafted fixtures
        # exercising the role / test-link wiring.
        index_dir = os.path.join(self.root, ".vc-context", "index")
        os.makedirs(index_dir, exist_ok=True)
        # Roles: two callback-handlers, one with a test linked.
        with open(os.path.join(index_dir, "agent_root.json"), "w") as fh:
            json.dump(
                {
                    "roles": {
                        "callback-handler": ["handle_foo", "handle_bar"],
                    },
                },
                fh,
            )
        with open(os.path.join(index_dir, "agent_symbols.json"), "w") as fh:
            json.dump(
                {
                    "handle_foo": {
                        "file": "bot/handlers/foo.py",
                        "line": 12,
                        "kind": "func",
                        "role": "callback-handler",
                    },
                    "handle_bar": {
                        "file": "bot/handlers/bar.py",
                        "line": 34,
                        "kind": "func",
                        "role": "callback-handler",
                    },
                },
                fh,
            )
        with open(os.path.join(index_dir, "agent_tests.json"), "w") as fh:
            json.dump(
                {
                    "handle_foo": {
                        "test_file": "tests/test_foo.py",
                        "test_function": "test_handle_foo",
                        "line": 5,
                    },
                },
                fh,
            )
        self.engine = QueryEngine(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_flags_handlers_without_linked_test(self) -> None:
        gaps = self.engine.find_handlers_without_tests(role="callback-handler")
        names = [g["name"] for g in gaps]
        self.assertEqual(names, ["handle_bar"])
        bar = gaps[0]
        self.assertEqual(bar["file"], "bot/handlers/bar.py")
        self.assertEqual(bar["line"], 34)
        self.assertEqual(bar["kind"], "func")
        self.assertEqual(bar["role"], "callback-handler")

    def test_umbrella_role_expands(self) -> None:
        """Default ``aiogram-handler`` umbrella covers
        ``callback-handler`` per ``_ROLE_UMBRELLAS``."""
        gaps = self.engine.find_handlers_without_tests()
        names = {g["name"] for g in gaps}
        self.assertIn("handle_bar", names)
        self.assertNotIn("handle_foo", names)

    def test_parity_ok_returns_empty(self) -> None:
        """When every handler has a test, list is empty."""
        # Add a test entry for handle_bar to close the gap.
        path = os.path.join(self.root, ".vc-context", "index", "agent_tests.json")
        with open(path) as fh:
            tests = json.load(fh)
        tests["handle_bar"] = {
            "test_file": "tests/test_bar.py",
            "test_function": "test_handle_bar",
            "line": 8,
        }
        with open(path, "w") as fh:
            json.dump(tests, fh)
        engine = QueryEngine(self.root)
        self.assertEqual(engine.find_handlers_without_tests(role="callback-handler"), [])


if __name__ == "__main__":
    unittest.main()
