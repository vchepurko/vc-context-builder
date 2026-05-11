"""Tests for the ``include_tests`` knob and its ``is_test_path``
helper. Verified across the search-tool surface so the feature stays
honest when query tools change their internal layout.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from _test_filter import filter_test_records, is_test_path
from query_engine import QueryEngine


def _write(path: str, body: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body))


class IsTestPathTests(unittest.TestCase):
    def test_tests_dir(self) -> None:
        self.assertTrue(is_test_path("tests/foo.py"))
        self.assertTrue(is_test_path("tests/nested/bar.py"))

    def test_submodule_tests_dir(self) -> None:
        self.assertTrue(is_test_path(".ai-context/tests/baz.py"))

    def test_production_paths_unaffected(self) -> None:
        self.assertFalse(is_test_path("services/cart.py"))
        self.assertFalse(is_test_path("bot/handlers/start.py"))

    def test_mytests_lookalike_does_not_match(self) -> None:
        """Folder ``mytests/`` is NOT a project test root — anchor at start."""
        self.assertFalse(is_test_path("mytests/foo.py"))

    def test_none_and_empty(self) -> None:
        self.assertFalse(is_test_path(None))
        self.assertFalse(is_test_path(""))

    def test_leading_dot_slash_stripped(self) -> None:
        self.assertTrue(is_test_path("./tests/foo.py"))

    def test_backslashes_normalised(self) -> None:
        self.assertTrue(is_test_path("tests\\foo.py"))


class FilterRecordsTests(unittest.TestCase):
    def test_drops_test_records_by_default(self) -> None:
        records = [
            {"file": "services/cart.py", "line": 10},
            {"file": "tests/test_cart.py", "line": 5},
            {"file": "bot/handlers/start.py", "line": 22},
        ]
        kept = filter_test_records(records, include_tests=False)
        self.assertEqual({r["file"] for r in kept}, {"services/cart.py", "bot/handlers/start.py"})

    def test_keeps_test_records_when_opted_in(self) -> None:
        records = [
            {"file": "tests/test_cart.py", "line": 5},
            {"file": "services/cart.py", "line": 10},
        ]
        kept = filter_test_records(records, include_tests=True)
        self.assertEqual(len(kept), 2)

    def test_handles_missing_file_field(self) -> None:
        """A record without ``file`` is kept — we don't know it's a test."""
        records = [{"line": 5}, {"file": "tests/x.py"}]
        kept = filter_test_records(records, include_tests=False)
        self.assertEqual(kept, [{"line": 5}])


class QueryEngineIntegrationTests(unittest.TestCase):
    """End-to-end through QueryEngine — exercise the knob from the
    same surface MCP clients call into."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="test_filter_eng_")
        # Minimal index: one production symbol + one test-only symbol.
        symbols = {
            "make_cart": {
                "file": "services/cart.py",
                "kind": "function",
                "line": 5,
                "end_line": 9,
            },
            "cart_fixture": {
                "file": "tests/fixtures/cart.py",
                "kind": "function",
                "line": 3,
                "end_line": 7,
            },
        }
        with open(os.path.join(self.root, "agent_symbols.json"), "w") as fh:
            json.dump(symbols, fh)
        with open(os.path.join(self.root, "agent_root.json"), "w") as fh:
            json.dump({"modules": [], "roles": {}}, fh)
        _write(
            os.path.join(self.root, "services", "cart.py"),
            """
            def make_cart():
                return {}
            """,
        )
        _write(
            os.path.join(self.root, "tests", "fixtures", "cart.py"),
            """
            def cart_fixture():
                return {}
            """,
        )

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_find_symbol_hides_test_symbols_by_default(self) -> None:
        eng = QueryEngine(self.root)
        self.assertIsNotNone(eng.find_symbol("make_cart"))
        # Test-defined symbol is invisible by default.
        self.assertIsNone(eng.find_symbol("cart_fixture"))
        # …and visible when opted in.
        self.assertIsNotNone(eng.find_symbol("cart_fixture", include_tests=True))

    def test_find_symbols_batch_honours_flag(self) -> None:
        eng = QueryEngine(self.root)
        default = eng.find_symbols(["make_cart", "cart_fixture"])
        self.assertIsNotNone(default["make_cart"])
        self.assertIsNone(default["cart_fixture"])
        opted_in = eng.find_symbols(
            ["make_cart", "cart_fixture"], include_tests=True
        )
        self.assertIsNotNone(opted_in["cart_fixture"])


if __name__ == "__main__":
    unittest.main()
