"""Tests for semantic doc section search.

Covers:
- extract_section_bodies: correct extraction of section text
- build_doc_store / search_doc_sections: store build + vector search
- query_engine.search_doc_text: semantic path when store is built,
  fallback to substring when store is empty
"""

from __future__ import annotations

import json
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

from indexers.markdown_index import extract_section_bodies, build_index
from stores.semantic_store import (
    build_doc_store,
    ensure_doc_store,
    search_doc_sections,
    LocalHashEmbeddingProvider,
)


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(content).lstrip())


def _make_docs_index(root: str) -> dict:
    """Build a real docs index from a temp project root."""
    return build_index(root)


class ExtractSectionBodiesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-docemb-")
        _write(
            os.path.join(self.root, "README.md"),
            """
            # Project Overview

            This project handles course registration.

            ## Installation

            Run pip install to set up.

            ## Usage

            Import the module and call register().
            """,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_returns_list_of_sections(self) -> None:
        idx = _make_docs_index(self.root)
        sections = extract_section_bodies(self.root, idx)
        self.assertIsInstance(sections, list)
        self.assertGreater(len(sections), 0)

    def test_section_has_required_keys(self) -> None:
        idx = _make_docs_index(self.root)
        sections = extract_section_bodies(self.root, idx)
        for s in sections:
            self.assertIn("id", s)
            self.assertIn("file", s)
            self.assertIn("title", s)
            self.assertIn("search_text", s)
            self.assertIn("line_start", s)

    def test_search_text_contains_body(self) -> None:
        idx = _make_docs_index(self.root)
        sections = extract_section_bodies(self.root, idx)
        install_sec = next((s for s in sections if "Installation" in s["title"]), None)
        self.assertIsNotNone(install_sec)
        self.assertIn("pip install", install_sec["search_text"])

    def test_empty_docs_returns_empty(self) -> None:
        sections = extract_section_bodies(self.root, {"docs": {}})
        self.assertEqual(sections, [])

    def test_body_truncated_to_max_chars(self) -> None:
        long_body = "word " * 300  # >600 chars
        _write(
            os.path.join(self.root, "long.md"),
            f"# Long Section\n\n{long_body}\n",
        )
        idx = _make_docs_index(self.root)
        sections = extract_section_bodies(self.root, idx, max_body_chars=100)
        long_sec = next((s for s in sections if s["file"] == "long.md"), None)
        self.assertIsNotNone(long_sec)
        # search_text = title + "\n" + body[:100] + "…"
        self.assertIn("…", long_sec["search_text"])

    def test_id_uses_anchor_when_present(self) -> None:
        idx = _make_docs_index(self.root)
        sections = extract_section_bodies(self.root, idx)
        for s in sections:
            if s.get("anchor"):
                self.assertIn(s["anchor"], s["id"])

    def test_missing_file_skipped_gracefully(self) -> None:
        idx = {
            "docs": {
                "nonexistent.md": {
                    "sections": [{"level": 1, "text": "Title", "line": 1, "end_line": 5, "anchor": "title"}]
                }
            }
        }
        sections = extract_section_bodies(self.root, idx)
        self.assertEqual(sections, [])


class BuildDocStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-docstore-")
        self.home = tempfile.mkdtemp(prefix="vc-docstore-home-")
        self._old_home = os.environ.get("VC_CONTEXT_HOME")
        os.environ["VC_CONTEXT_HOME"] = self.home
        _write(
            os.path.join(self.root, "SPEC.md"),
            """
            # Authentication

            Handles token validation and session management.

            ## Token refresh

            Tokens expire after 30 minutes and must be refreshed.

            ## Session store

            Sessions are stored in Redis for fast lookup.
            """,
        )
        idx = _make_docs_index(self.root)
        self.sections = extract_section_bodies(self.root, idx)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("VC_CONTEXT_HOME", None)
        else:
            os.environ["VC_CONTEXT_HOME"] = self._old_home
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_build_returns_section_count(self) -> None:
        result = build_doc_store(self.root, self.sections, provider=LocalHashEmbeddingProvider())
        self.assertGreater(result["sections"], 0)
        self.assertEqual(result["sections"], len(self.sections))

    def test_build_idempotent_via_ensure(self) -> None:
        r1 = ensure_doc_store(self.root, self.sections, provider=LocalHashEmbeddingProvider())
        r2 = ensure_doc_store(self.root, self.sections, provider=LocalHashEmbeddingProvider())
        self.assertTrue(r1.get("rebuilt"))
        self.assertFalse(r2.get("rebuilt"))

    def test_search_returns_results(self) -> None:
        build_doc_store(self.root, self.sections, provider=LocalHashEmbeddingProvider())
        hits = search_doc_sections(
            self.root, "token refresh", provider=LocalHashEmbeddingProvider()
        )
        self.assertIsNotNone(hits)
        self.assertIsInstance(hits, list)
        self.assertGreater(len(hits), 0)

    def test_search_result_has_required_keys(self) -> None:
        build_doc_store(self.root, self.sections, provider=LocalHashEmbeddingProvider())
        hits = search_doc_sections(
            self.root, "session", provider=LocalHashEmbeddingProvider()
        )
        self.assertIsNotNone(hits)
        for h in hits:  # type: ignore[union-attr]
            self.assertIn("file", h)
            self.assertIn("title", h)
            self.assertIn("score", h)
            self.assertIn("line", h)

    def test_search_empty_table_returns_none(self) -> None:
        # Fresh root with no store built
        fresh_root = tempfile.mkdtemp(prefix="vc-docstore-fresh-")
        try:
            hits = search_doc_sections(fresh_root, "anything", provider=LocalHashEmbeddingProvider())
            self.assertIsNone(hits)
        finally:
            shutil.rmtree(fresh_root, ignore_errors=True)

    def test_file_filter_scopes_results(self) -> None:
        build_doc_store(self.root, self.sections, provider=LocalHashEmbeddingProvider())
        hits = search_doc_sections(
            self.root,
            "token",
            file_filter="nonexistent.md",
            provider=LocalHashEmbeddingProvider(),
        )
        self.assertIsNotNone(hits)
        self.assertEqual(hits, [])

    def test_empty_query_returns_empty(self) -> None:
        build_doc_store(self.root, self.sections, provider=LocalHashEmbeddingProvider())
        hits = search_doc_sections(self.root, "   ", provider=LocalHashEmbeddingProvider())
        self.assertEqual(hits, [])


class QueryEngineSearchDocTextTests(unittest.TestCase):
    """search_doc_text falls back to substring when doc store not built."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-qe-docs-")
        self.home = tempfile.mkdtemp(prefix="vc-qe-docs-home-")
        self._old_home = os.environ.get("VC_CONTEXT_HOME")
        os.environ["VC_CONTEXT_HOME"] = self.home
        _write(
            os.path.join(self.root, "GUIDE.md"),
            """
            # Setup

            Install dependencies with pip.

            ## Database

            Configure the database host in settings.
            """,
        )
        # Build docs index (no embeddings yet)
        from indexers.markdown_index import write_index
        from paths import index_path

        os.makedirs(os.path.join(self.root, ".vc-context", "index"), exist_ok=True)
        write_index(self.root, os.path.join(self.root, ".vc-context", "index", "agent_docs_index.json"))

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("VC_CONTEXT_HOME", None)
        else:
            os.environ["VC_CONTEXT_HOME"] = self._old_home
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_fallback_substring_when_no_store(self) -> None:
        from query_engine import QueryEngine

        engine = QueryEngine(self.root)
        hits = engine.search_doc_text("database")
        # Falls back to substring — should find "database" in content
        self.assertIsInstance(hits, list)
        self.assertGreater(len(hits), 0)

    def test_semantic_path_when_store_built(self) -> None:
        from query_engine import QueryEngine
        from indexers.markdown_index import build_index
        from stores.semantic_store import build_doc_store, LocalHashEmbeddingProvider
        from indexers.markdown_index import extract_section_bodies

        idx = build_index(self.root)
        sections = extract_section_bodies(self.root, idx)
        build_doc_store(self.root, sections, provider=LocalHashEmbeddingProvider())

        engine = QueryEngine(self.root)
        hits = engine.search_doc_text("install dependencies")
        self.assertIsInstance(hits, list)
        self.assertGreater(len(hits), 0)
        # Semantic results have 'score' field, grep results have 'snippet'
        self.assertIn("score", hits[0])

    def test_regex_forces_grep_path(self) -> None:
        from query_engine import QueryEngine
        from indexers.markdown_index import build_index
        from stores.semantic_store import build_doc_store, LocalHashEmbeddingProvider
        from indexers.markdown_index import extract_section_bodies

        idx = build_index(self.root)
        sections = extract_section_bodies(self.root, idx)
        build_doc_store(self.root, sections, provider=LocalHashEmbeddingProvider())

        engine = QueryEngine(self.root)
        hits = engine.search_doc_text(r"data\w+", regex=True)
        self.assertIsInstance(hits, list)
        # Grep results have 'snippet' not 'score'
        if hits:
            self.assertIn("snippet", hits[0])


if __name__ == "__main__":
    unittest.main()
