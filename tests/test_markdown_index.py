"""Unit tests for the markdown docs index + MCP-surface query helpers.

The index is built by walking a temp tree of ``.md`` files; the
QueryEngine then reads the artefact through the same path the MCP
dispatcher uses. Together they pin:

* Section TOC extraction (headings, end_line scope rule, code-fence
  skipping).
* Link extraction + internal/external classification.
* All five public helpers: get_toc, find_section, list_docs,
  find_xref, link_graph (with broken-link detection).
"""

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

import markdown_index
from query_engine import QueryEngine


def _write(path: str, body: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


class BuildIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-md-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_empty_root_returns_empty_docs(self) -> None:
        index = markdown_index.build_index(self.root)
        self.assertEqual(index["docs"], {})
        self.assertIn("generated_at", index)

    def test_single_file_extracts_sections(self) -> None:
        _write(
            os.path.join(self.root, "doc.md"),
            "# Title\n\nIntro paragraph.\n\n## Section A\n\nContent A.\n\n"
            "## Section B\n\nContent B.\n",
        )
        index = markdown_index.build_index(self.root)
        rec = index["docs"]["doc.md"]
        self.assertEqual(rec["top_header"], "Title")
        secs = rec["sections"]
        self.assertEqual([s["text"] for s in secs], ["Title", "Section A", "Section B"])
        self.assertEqual([s["level"] for s in secs], [1, 2, 2])

    def test_end_line_respects_heading_scope(self) -> None:
        """``## A`` runs until ``## B`` (same-level boundary); ``### A1``
        runs until ``## B`` (shallower-level boundary). EOF terminates
        the last section."""
        _write(
            os.path.join(self.root, "doc.md"),
            "\n".join(
                [
                    "## A",  # line 1
                    "a-body",  # 2
                    "### A1",  # 3
                    "a1-body",  # 4
                    "## B",  # 5
                    "b-body",  # 6
                ]
            )
            + "\n",
        )
        secs = markdown_index.build_index(self.root)["docs"]["doc.md"]["sections"]
        by_text = {s["text"]: s for s in secs}
        # ## A starts line 1, next same-or-shallower is ## B at line 5
        # → end_line = 4.
        self.assertEqual(by_text["A"]["end_line"], 4)
        # ### A1 starts line 3, next shallower (##) at 5 → end_line = 4.
        self.assertEqual(by_text["A1"]["end_line"], 4)
        # ## B is the last section → end_line = total lines (6).
        self.assertEqual(by_text["B"]["end_line"], 6)

    def test_code_fence_blocks_dont_count_as_headings(self) -> None:
        """Hash-prefixed lines inside ```` ``` ```` are comments, not
        headings — the indexer must ignore them."""
        _write(
            os.path.join(self.root, "doc.md"),
            "# Real H1\n\n"
            "```python\n# this is a Python comment\n## not a heading\n```\n\n"
            "## After fence\n",
        )
        secs = markdown_index.build_index(self.root)["docs"]["doc.md"]["sections"]
        texts = [s["text"] for s in secs]
        self.assertEqual(texts, ["Real H1", "After fence"])

    def test_links_split_into_internal_external(self) -> None:
        _write(
            os.path.join(self.root, "doc.md"),
            "See [README](README.md), [docs](docs/x.md), and [google](https://google.com).\n",
        )
        rec = markdown_index.build_index(self.root)["docs"]["doc.md"]
        targets = {(link["target"], link["external"]) for link in rec["links"]}
        self.assertIn(("README.md", False), targets)
        self.assertIn(("docs/x.md", False), targets)
        self.assertIn(("https://google.com", True), targets)


class QueryHelperTests(unittest.TestCase):
    """Drive QueryEngine through ``agent_docs_index.json`` on disk —
    same path the MCP dispatcher uses."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-md-query-")
        self.addCleanup(shutil.rmtree, self.root, True)
        # Two docs with a cross-link, plus one broken link.
        _write(
            os.path.join(self.root, "README.md"),
            "# Project\n\nSee [Ops](docs/OPS.md) and [Missing](docs/ghost.md).\n",
        )
        _write(
            os.path.join(self.root, "docs", "OPS.md"),
            "# Operations\n\n"
            "## Logs\n\nstuff\n\n"
            "## Rotation\n\n"
            "rotate ACTING_USER_HMAC_KEY quarterly.\n",
        )
        # Persist the index where QueryEngine expects it.
        out = os.path.join(self.root, "agent_docs_index.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(markdown_index.build_index(self.root), fh)
        self.engine = QueryEngine(self.root)

    def test_get_doc_toc_returns_section_list(self) -> None:
        toc = self.engine.get_doc_toc("docs/OPS.md") or []
        self.assertEqual([s["text"] for s in toc], ["Operations", "Logs", "Rotation"])

    def test_get_doc_toc_unknown_file_returns_none(self) -> None:
        self.assertIsNone(self.engine.get_doc_toc("nope.md"))

    def test_get_doc_toc_max_level_trims_deep_headings(self) -> None:
        """``max_level`` drops headings deeper than the cap. Pins the
        Phase 2 'light TOC' behaviour — agent passes ``max_level=2``
        to skip ``###``+ subsections on long docs."""
        # Build a fresh fixture with mixed levels so the cap has
        # something to actually trim (OPS.md only has H1+H2).
        _write(
            os.path.join(self.root, "deep.md"),
            "# Root\n\n## Top A\n\n### Sub A1\n\n### Sub A2\n\n## Top B\n\n#### Deep B1\n\n",
        )
        out = os.path.join(self.root, "agent_docs_index.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(markdown_index.build_index(self.root), fh)
        engine = QueryEngine(self.root)

        full = engine.get_doc_toc("deep.md") or []
        levels_full = [s["level"] for s in full]
        self.assertEqual(levels_full, [1, 2, 3, 3, 2, 4])

        trimmed = engine.get_doc_toc("deep.md", max_level=2) or []
        self.assertEqual([s["text"] for s in trimmed], ["Root", "Top A", "Top B"])

        # Out-of-range / invalid values fall through to full TOC at
        # the dispatcher boundary; the helper layer respects the value
        # literally — H1-only when max_level=1.
        h1_only = engine.get_doc_toc("deep.md", max_level=1) or []
        self.assertEqual([s["text"] for s in h1_only], ["Root"])

    def test_find_doc_section_fuzzy_match(self) -> None:
        sec = self.engine.find_doc_section("docs/OPS.md", "rotat")
        self.assertIsNotNone(sec)
        assert sec is not None  # mypy nudge
        self.assertEqual(sec["text"], "Rotation")
        # end_line must follow the scope rule (EOF for last section).
        self.assertGreater(sec["end_line"], sec["line"])

    def test_find_doc_section_exact_mode_rejects_substring(self) -> None:
        """``fuzzy=False`` requires equality — substring 'log' should
        NOT match 'Logs'."""
        self.assertIsNone(self.engine.find_doc_section("docs/OPS.md", "log", fuzzy=False))
        self.assertIsNotNone(self.engine.find_doc_section("docs/OPS.md", "Logs", fuzzy=False))

    def _ideas_engine(self) -> QueryEngine:
        """Build an IDEAS.md-style fixture with numbered headings and
        slug anchors that exercise the loose-lookup selectors."""
        _write(
            os.path.join(self.root, "IDEAS.md"),
            "# Ideas\n\n"
            "## 27. Unified User Model\n\nbody\n\n"
            "## 28. Authz Hardening — signed X-Acting-User-Id\n\nbody\n\n"
            "## 28.1 Sub-step\n\nbody\n\n"
            "## 31. Booking with Calendar\n\nbody\n\n",
        )
        out = os.path.join(self.root, "agent_docs_index.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(markdown_index.build_index(self.root), fh)
        return QueryEngine(self.root)

    def test_find_doc_section_anchor_prefix_match(self) -> None:
        """``anchor="31"`` matches the slug ``31-...`` regardless of
        what comes after the number."""
        engine = self._ideas_engine()
        sec = engine.find_doc_section("IDEAS.md", anchor="31")
        self.assertIsNotNone(sec)
        assert sec is not None  # mypy nudge
        self.assertIn("31.", sec["text"])
        self.assertTrue(sec["anchor"].startswith("31"))

    def test_find_doc_section_number_selector(self) -> None:
        """``number=27`` finds the heading whose text starts with
        '27.' / '27 ' / '27<eof>'."""
        engine = self._ideas_engine()
        sec = engine.find_doc_section("IDEAS.md", number=27)
        self.assertIsNotNone(sec)
        assert sec is not None
        self.assertTrue(sec["text"].startswith("27."))
        # Negative: a number not present returns None.
        self.assertIsNone(engine.find_doc_section("IDEAS.md", number=99))

    def test_find_doc_section_heading_ranks_shortest_first(self) -> None:
        """``heading="Authz"`` prefers '28.1 Sub-step' over the
        longer '28. Authz Hardening — signed X-Acting-User-Id'? No —
        the substring 'Authz' isn't in '28.1 Sub-step'. Verify the
        single matching section is returned, then verify shortest-
        first ranking by hitting a substring present in multiple
        headings ('28')."""
        engine = self._ideas_engine()
        sec = engine.find_doc_section("IDEAS.md", heading="Authz")
        self.assertIsNotNone(sec)
        assert sec is not None
        self.assertIn("Authz", sec["text"])
        # Shortest-first: 'Sub-step' heading is shortest among the 28* family.
        ranked = engine.find_doc_section("IDEAS.md", heading="28")
        self.assertIsNotNone(ranked)
        assert ranked is not None
        self.assertEqual(ranked["text"], "28.1 Sub-step")

    def test_search_doc_text_attaches_section_context(self) -> None:
        """Hits in ``## Rotation`` carry section metadata."""
        results = self.engine.search_doc_text("ACTING_USER_HMAC_KEY")
        self.assertEqual(len(results), 1)
        hit = results[0]
        self.assertEqual(hit["file"], "docs/OPS.md")
        self.assertIn("ACTING_USER_HMAC_KEY", hit["snippet"])
        self.assertIsNotNone(hit["section"])
        self.assertEqual(hit["section"]["heading"], "Rotation")
        self.assertEqual(hit["section"]["level"], 2)

    def test_search_doc_text_file_scope(self) -> None:
        """``file=`` restricts search to one doc; unknown file → []."""
        results = self.engine.search_doc_text("the", file="docs/OPS.md")
        self.assertTrue(all(r["file"] == "docs/OPS.md" for r in results))
        self.assertEqual(self.engine.search_doc_text("foo", file="nope.md"), [])

    def test_search_doc_text_regex_mode(self) -> None:
        """``regex=True`` honours Python regex semantics."""
        engine = self._ideas_engine()
        results = engine.search_doc_text(r"^## 2[78]\.", regex=True, file="IDEAS.md")
        # Three section openers begin with '## 27.' / '## 28.' (28.1 starts with '## 28.1', regex
        # ^## 2[78]\. requires '.' after digit so 28.1 doesn't match).
        texts = {r["snippet"] for r in results}
        self.assertTrue(any(t.startswith("## 27.") for t in texts))
        self.assertTrue(any(t.startswith("## 28.") for t in texts))

    def test_search_doc_text_case_sensitive(self) -> None:
        """``case_sensitive=True`` rejects different-case hits."""
        self.assertEqual(
            self.engine.search_doc_text("rotation", case_sensitive=True),
            [],
        )
        results = self.engine.search_doc_text("Rotation", case_sensitive=True)
        # The heading line itself matches.
        self.assertTrue(
            any(r["section"] and r["section"]["heading"] == "Rotation" for r in results),
        )

    def test_find_doc_section_selector_priority_anchor_wins(self) -> None:
        """When multiple selectors are passed, ``anchor`` wins."""
        engine = self._ideas_engine()
        sec = engine.find_doc_section(
            "IDEAS.md",
            anchor="27",
            number=31,
            heading="Booking",
        )
        self.assertIsNotNone(sec)
        assert sec is not None
        self.assertTrue(sec["anchor"].startswith("27"))

    def test_list_docs_filters_by_prefix(self) -> None:
        # All docs
        every = self.engine.list_docs()
        paths = {r["path"] for r in every}
        self.assertEqual(paths, {"README.md", "docs/OPS.md"})
        # Prefix-filtered
        docs_only = self.engine.list_docs(path_prefix="docs/")
        self.assertEqual({r["path"] for r in docs_only}, {"docs/OPS.md"})

    def test_find_doc_xref_finds_term_with_line_context(self) -> None:
        hits = self.engine.find_doc_xref("ACTING_USER_HMAC_KEY")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["file"], "docs/OPS.md")
        self.assertIn("ACTING_USER_HMAC_KEY", hits[0]["snippet"])

    def test_find_doc_xref_case_insensitive_by_default(self) -> None:
        # 'operations' is in 'Operations' heading — case-insensitive default.
        hits = self.engine.find_doc_xref("operations")
        self.assertTrue(any("Operations" in h["snippet"] for h in hits))

    def test_docs_link_graph_reports_broken_link(self) -> None:
        graph = self.engine.docs_link_graph()
        # Forward edge from README to OPS resolved relative to root.
        self.assertIn("docs/OPS.md", graph["forward"]["README.md"])
        # Broken link to docs/ghost.md is reported.
        broken_targets = [b["target"] for b in graph["broken"]]
        self.assertIn("docs/ghost.md", broken_targets)


if __name__ == "__main__":
    unittest.main()
