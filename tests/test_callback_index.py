"""Tests for the aiogram callback_data index (Feature D).

Covers:
* AST extraction of ``F.data == "x"``, ``F.data.startswith("x")``,
  ``F.data.in_([...])``;
* exact-vs-prefix lookup precedence in ``find_callback``;
* end-to-end ``QueryEngine.find_callback`` against an on-disk index;
* MCP-server tool registration so an agent can actually call it.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from indexers.callback_index import (
    CALLBACKS_FILENAME,
    collect_callbacks,
    find_callback,
    write_callback_index,
)
from mcp_server import _tool_specs
from query_engine import QueryEngine

_FIXTURE_SOURCE = textwrap.dedent("""
    from aiogram import F, Router
    router = Router()

    @router.callback_query(F.data == "adm:staff_add")
    async def adm_staff_add(callback): ...

    @router.callback_query(F.data.startswith("adm:staff_detail:"))
    async def adm_staff_detail(callback): ...

    @router.callback_query(F.data.in_(["lang:uk", "lang:en"]))
    async def lang_pick(callback): ...

    @router.callback_query(some_custom_filter)
    async def weird(callback): ...
""")


class TestCallbackIndexExtraction(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="cb_idx_")
        self.fname = os.path.join(self.tmpdir, "handlers.py")
        with open(self.fname, "w", encoding="utf-8") as fh:
            fh.write(_FIXTURE_SOURCE)

    def tearDown(self) -> None:
        # Use shutil.rmtree — the write now lands under
        # ``.vc-context/index/`` so the tmpdir contains a sub-tree,
        # not just flat files.
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_exact_filter_indexed(self) -> None:
        idx = collect_callbacks(self.tmpdir)
        self.assertIn("adm:staff_add", idx)
        records = idx["adm:staff_add"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "exact")
        self.assertEqual(records[0]["handler"], "adm_staff_add")
        self.assertEqual(records[0]["file"], "handlers.py")

    def test_prefix_filter_indexed_with_kind(self) -> None:
        idx = collect_callbacks(self.tmpdir)
        self.assertIn("adm:staff_detail:", idx)
        rec = idx["adm:staff_detail:"][0]
        self.assertEqual(rec["kind"], "prefix")
        self.assertEqual(rec["handler"], "adm_staff_detail")

    def test_in_filter_explodes_into_multiple_exact_entries(self) -> None:
        idx = collect_callbacks(self.tmpdir)
        self.assertIn("lang:uk", idx)
        self.assertIn("lang:en", idx)
        self.assertEqual(idx["lang:uk"][0]["handler"], "lang_pick")
        self.assertEqual(idx["lang:uk"][0]["kind"], "exact")

    def test_unknown_filter_shape_is_skipped(self) -> None:
        idx = collect_callbacks(self.tmpdir)
        # The ``some_custom_filter`` decorator shouldn't produce any
        # entries — we only index recognised F.data shapes.
        for records in idx.values():
            for r in records:
                self.assertNotEqual(r["handler"], "weird")


class TestFindCallbackLookup(unittest.TestCase):
    def setUp(self) -> None:
        self.idx = {
            "adm:staff_add": [{"kind": "exact", "handler": "h_add", "file": "x.py", "line": 1}],
            "adm:staff_detail:": [
                {"kind": "prefix", "handler": "h_detail", "file": "x.py", "line": 2}
            ],
            "adm:staff_detail:role:": [
                {"kind": "prefix", "handler": "h_detail_role", "file": "x.py", "line": 3}
            ],
        }

    def test_exact_match_wins_over_prefix(self) -> None:
        out = find_callback(self.idx, "adm:staff_add")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["handler"], "h_add")
        self.assertEqual(out[0]["kind"], "exact")

    def test_longest_prefix_wins(self) -> None:
        out = find_callback(self.idx, "adm:staff_detail:role:42")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["handler"], "h_detail_role")

    def test_short_prefix_used_when_only_one_matches(self) -> None:
        out = find_callback(self.idx, "adm:staff_detail:42")
        self.assertEqual(out[0]["handler"], "h_detail")

    def test_empty_data_returns_empty(self) -> None:
        self.assertEqual(find_callback(self.idx, ""), [])

    def test_no_match_returns_empty(self) -> None:
        self.assertEqual(find_callback(self.idx, "completely:unknown"), [])


class TestQueryEngineFindCallback(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="cb_qe_")
        self.fixture = os.path.join(self.tmpdir, "handlers.py")
        with open(self.fixture, "w", encoding="utf-8") as fh:
            fh.write(_FIXTURE_SOURCE)
        # Build the index and write it where QueryEngine expects.
        idx = collect_callbacks(self.tmpdir)
        write_callback_index(self.tmpdir, idx)
        # QueryEngine also tries to load agent_root.json on some paths,
        # but find_callback only needs the callbacks file.
        self.engine = QueryEngine(self.tmpdir)

    def tearDown(self) -> None:
        # Use shutil.rmtree — the write now lands under
        # ``.vc-context/index/`` so the tmpdir contains a sub-tree,
        # not just flat files.
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_round_trip_through_engine(self) -> None:
        out = self.engine.find_callback("adm:staff_add")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["handler"], "adm_staff_add")

    def test_prefix_round_trip_through_engine(self) -> None:
        out = self.engine.find_callback("adm:staff_detail:7")
        self.assertEqual(out[0]["handler"], "adm_staff_detail")

    def test_missing_index_degrades_gracefully(self) -> None:
        # File now lives under .vc-context/index/ — remove it there.
        from paths import index_path

        os.remove(index_path(self.tmpdir, CALLBACKS_FILENAME))
        engine = QueryEngine(self.tmpdir)
        self.assertEqual(engine.find_callback("anything"), [])


class TestMcpToolRegistration(unittest.TestCase):
    def test_find_callback_listed_in_tool_specs(self) -> None:
        names = {spec["name"] for spec in _tool_specs()}
        self.assertIn("find_callback", names)


if __name__ == "__main__":
    unittest.main()
