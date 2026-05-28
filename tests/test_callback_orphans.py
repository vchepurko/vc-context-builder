"""Unit tests for ``find_orphan_callbacks`` — buttons whose
``callback_data="..."`` is never picked up by a registered handler.
"""

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

import indexers.callback_index as callback_index
from query_engine import QueryEngine


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(content).lstrip())


class FindOrphansTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-orphans-")
        # Handlers: exact 'adm:foo', prefix 'adm:bar:'.
        _write(
            os.path.join(self.root, "bot", "handlers", "admin.py"),
            """
            from aiogram import F, Router
            router = Router()

            @router.callback_query(F.data == "adm:foo")
            async def handle_foo(call):
                pass

            @router.callback_query(F.data.startswith("adm:bar:"))
            async def handle_bar(call):
                pass
            """,
        )
        # Buttons: one good ('adm:foo'), one good prefix ('adm:bar:42'),
        # one orphan ('adm:ghost'), one dynamic (f-string — must be skipped).
        _write(
            os.path.join(self.root, "bot", "handlers", "menu.py"),
            """
            from aiogram.types import InlineKeyboardButton

            def build_menu(user_id: int):
                return [
                    InlineKeyboardButton(text="Foo", callback_data="adm:foo"),
                    InlineKeyboardButton(text="Bar", callback_data="adm:bar:42"),
                    InlineKeyboardButton(text="Ghost", callback_data="adm:ghost"),
                    InlineKeyboardButton(text="Dynamic", callback_data=f"adm:dyn:{user_id}"),
                ]
            """,
        )
        # Same orphan string inside tests/ — must be skipped by default.
        _write(
            os.path.join(self.root, "tests", "test_buttons.py"),
            """
            from aiogram.types import InlineKeyboardButton

            def test_fixture():
                return InlineKeyboardButton(text="x", callback_data="adm:test-fixture")
            """,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _engine_with_index(self) -> QueryEngine:
        idx = callback_index.collect_callbacks(self.root)
        callback_index.write_callback_index(self.root, idx)
        return QueryEngine(self.root)

    def test_orphans_lists_dead_buttons_only(self) -> None:
        engine = self._engine_with_index()
        orphans = engine.find_orphan_callbacks()
        datas = {o["data"] for o in orphans}
        self.assertIn("adm:ghost", datas)
        # Registered buttons must NOT be flagged.
        self.assertNotIn("adm:foo", datas)
        self.assertNotIn("adm:bar:42", datas)
        # f-string can't be resolved → silently skipped, not flagged.
        self.assertFalse(any("adm:dyn" in d for d in datas))
        # tests/ excluded by default.
        self.assertNotIn("adm:test-fixture", datas)

    def test_include_tests_surfaces_test_fixtures(self) -> None:
        engine = self._engine_with_index()
        orphans = engine.find_orphan_callbacks(include_tests=True)
        datas = {o["data"] for o in orphans}
        self.assertIn("adm:test-fixture", datas)
        self.assertIn("adm:ghost", datas)

    def test_record_shape(self) -> None:
        engine = self._engine_with_index()
        orphans = engine.find_orphan_callbacks()
        ghost = next(o for o in orphans if o["data"] == "adm:ghost")
        self.assertEqual(ghost["file"], "bot/handlers/menu.py")
        self.assertIsInstance(ghost["line"], int)
        self.assertGreater(ghost["line"], 0)


if __name__ == "__main__":
    unittest.main()
