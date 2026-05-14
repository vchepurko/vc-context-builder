"""Unit tests for ``find_in_file`` — surgical single-file grep."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import textwrap
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

from query_engine import QueryEngine


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(content).lstrip())


class FindInFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-grep-")
        _write(
            os.path.join(self.root, "Checkout.js"),
            """
            export class Checkout {
                init() { return this.fetchPrice(); }
                fetchPrice() { return fetch('/api/price'); }
                fetchTotal() { return fetch('/api/total'); }
            }
            """,
        )
        self.engine = QueryEngine(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_substring_match_default_case_insensitive(self) -> None:
        hits = self.engine.find_in_file("Checkout.js", "FETCH")
        self.assertEqual(len(hits), 3)  # fetchPrice/fetchTotal x decls + 2 fetch( calls
        for h in hits:
            self.assertIsInstance(h["line"], int)
            self.assertIn("fetch", h["text"].lower())

    def test_case_sensitive_distinguishes(self) -> None:
        # 'Fetch' (capital F) doesn't appear — fetchPrice/fetchTotal are camelCase.
        self.assertEqual(
            self.engine.find_in_file("Checkout.js", "Fetch", case_sensitive=True),
            [],
        )
        hits = self.engine.find_in_file("Checkout.js", "fetch", case_sensitive=True)
        self.assertGreater(len(hits), 0)

    def test_regex_mode(self) -> None:
        hits = self.engine.find_in_file(
            "Checkout.js",
            r"fetch\((['\"])/api/\w+\1\)",
            use_regex=True,
        )
        self.assertEqual(len(hits), 2)

    def test_limit_caps_output(self) -> None:
        hits = self.engine.find_in_file("Checkout.js", "fetch", limit=1)
        self.assertEqual(len(hits), 1)

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(self.engine.find_in_file("nope.js", "anything"), [])

    def test_escape_rejected(self) -> None:
        """Traversal outside project_root must be refused."""
        self.assertEqual(
            self.engine.find_in_file("../etc/passwd", "root"),
            [],
        )

    def test_empty_pattern_returns_empty(self) -> None:
        self.assertEqual(self.engine.find_in_file("Checkout.js", ""), [])


if __name__ == "__main__":
    unittest.main()
