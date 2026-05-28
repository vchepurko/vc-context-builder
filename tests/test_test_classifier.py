"""Tests for the unit/integration test categoriser (Feature H)."""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mcp_server import _tool_specs
from query_engine import QueryEngine
from test_analysis.test_classifier import (
    TEST_CATEGORIES_FILENAME,
    category_summary,
    classify_test_file,
    collect_test_categories,
    lookup_tests_by_category,
    write_test_categories,
)


def _write(path: str, body: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body))


class _Fixture:
    """Synthetic test tree with one example per expected category."""

    def __init__(self) -> None:
        self.root = tempfile.mkdtemp(prefix="tc_")
        # Integration via httpx ASGI transport.
        _write(
            os.path.join(self.root, "tests", "test_http_route.py"),
            """
            import httpx
            from httpx import ASGITransport
            def test_route():
                pass
        """,
        )
        # Integration via direct DB session.
        _write(
            os.path.join(self.root, "tests", "test_db_round_trip.py"),
            """
            from database.db import async_session
            async def test_db():
                async with async_session() as s:
                    pass
        """,
        )
        # Integration via explicit pytest marker.
        _write(
            os.path.join(self.root, "tests", "test_marked_integration.py"),
            """
            import pytest
            pytestmark = pytest.mark.integration
            def test_thing():
                pass
        """,
        )
        # Pure unit — only mocks.
        _write(
            os.path.join(self.root, "tests", "test_pure_unit.py"),
            """
            from unittest.mock import AsyncMock, MagicMock, patch
            def test_handler():
                pass
        """,
        )
        # Unknown — no signals at all.
        _write(
            os.path.join(self.root, "tests", "test_no_signals.py"),
            """
            def test_pure():
                assert 1 + 1 == 2
        """,
        )
        # Non-test python file — must be skipped by the walker.
        _write(
            os.path.join(self.root, "tests", "helpers.py"),
            """
            from httpx import ASGITransport
        """,
        )

    def cleanup(self) -> None:
        for cur, dirs, files in os.walk(self.root, topdown=False):
            for f in files:
                os.remove(os.path.join(cur, f))
            for d in dirs:
                os.rmdir(os.path.join(cur, d))
        os.rmdir(self.root)


class TestClassifyFile(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_httpx_asgi_marks_integration(self) -> None:
        rec = classify_test_file(os.path.join(self.fx.root, "tests", "test_http_route.py"))
        self.assertEqual(rec["category"], "integration")
        # Substring match — the canonical hint string lands in signals.
        self.assertTrue(any("httpx" in s for s in rec["signals"]))

    def test_db_session_marks_integration(self) -> None:
        rec = classify_test_file(os.path.join(self.fx.root, "tests", "test_db_round_trip.py"))
        self.assertEqual(rec["category"], "integration")
        self.assertTrue(any("async_session" in s for s in rec["signals"]))

    def test_pytestmark_integration_recognised(self) -> None:
        rec = classify_test_file(os.path.join(self.fx.root, "tests", "test_marked_integration.py"))
        self.assertEqual(rec["category"], "integration")
        self.assertIn("pytestmark", rec["signals"])

    def test_pure_unit_only_mocks(self) -> None:
        rec = classify_test_file(os.path.join(self.fx.root, "tests", "test_pure_unit.py"))
        self.assertEqual(rec["category"], "unit")

    def test_no_signals_returns_unknown(self) -> None:
        rec = classify_test_file(os.path.join(self.fx.root, "tests", "test_no_signals.py"))
        self.assertEqual(rec["category"], "unknown")
        self.assertEqual(rec["signals"], [])

    def test_missing_file_safe_default(self) -> None:
        rec = classify_test_file(os.path.join(self.fx.root, "tests", "no_such.py"))
        self.assertEqual(rec, {"category": "unknown", "signals": []})


class TestCollectAndQuery(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()
        self.index = collect_test_categories(self.fx.root)

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_only_test_prefixed_files_indexed(self) -> None:
        # helpers.py must NOT appear — it isn't named test_*.py.
        self.assertNotIn("tests/helpers.py", self.index)

    def test_summary_counts_by_category(self) -> None:
        summary = category_summary(self.index)
        self.assertEqual(summary.get("integration"), 3)
        self.assertEqual(summary.get("unit"), 1)
        self.assertEqual(summary.get("unknown"), 1)

    def test_tests_by_category_returns_sorted_paths(self) -> None:
        ints = lookup_tests_by_category(self.index, "integration")
        self.assertEqual(ints, sorted(ints))
        self.assertIn("tests/test_http_route.py", ints)
        self.assertIn("tests/test_db_round_trip.py", ints)
        self.assertIn("tests/test_marked_integration.py", ints)

    def test_tests_by_category_unknown_returns_empty(self) -> None:
        self.assertEqual(lookup_tests_by_category(self.index, "no-such"), [])
        self.assertEqual(lookup_tests_by_category(self.index, ""), [])


class TestQueryEngineWiring(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()
        write_test_categories(self.fx.root, collect_test_categories(self.fx.root))
        self.engine = QueryEngine(self.fx.root)

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_classify_tests_round_trip(self) -> None:
        out = self.engine.classify_tests()
        self.assertIn("summary", out)
        self.assertIn("files", out)
        self.assertEqual(out["summary"].get("integration"), 3)

    def test_tests_by_category_round_trip(self) -> None:
        self.assertIn(
            "tests/test_pure_unit.py",
            self.engine.tests_by_category("unit"),
        )

    def test_missing_artifact_degrades_gracefully(self) -> None:
        from paths import index_path

        os.remove(index_path(self.fx.root, TEST_CATEGORIES_FILENAME))
        engine = QueryEngine(self.fx.root)
        self.assertEqual(engine.classify_tests(), {"summary": {}, "files": {}})
        self.assertEqual(engine.tests_by_category("unit"), [])


class TestMcpToolWiring(unittest.TestCase):
    def test_classify_tests_listed(self) -> None:
        names = {spec["name"] for spec in _tool_specs()}
        self.assertIn("classify_tests", names)
        self.assertIn("tests_by_category", names)

    def test_classify_tests_takes_no_args(self) -> None:
        spec = next(s for s in _tool_specs() if s["name"] == "classify_tests")
        self.assertEqual(spec["inputSchema"].get("required", []), [])

    def test_tests_by_category_requires_arg(self) -> None:
        spec = next(s for s in _tool_specs() if s["name"] == "tests_by_category")
        self.assertEqual(spec["inputSchema"].get("required", []), ["category"])


if __name__ == "__main__":
    unittest.main()
