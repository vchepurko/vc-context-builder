"""Unit tests for ``find_anti_patterns`` — the registered detector
registry. Currently covers ``aiogram-state-check-in-body``."""

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


class FindAntiPatternsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-anti-")
        # GOOD: state filter + F-expression.
        _write(
            os.path.join(self.root, "bot", "handlers", "good.py"),
            """
            from aiogram import F, Router
            router = Router()

            class AddStaffState:
                waiting_user_id = "state"

            @router.message(AddStaffState.waiting_user_id, F.text)
            async def take_user_id(message, state):
                pass
            """,
        )
        # BAD: bare F.text — silent-dispatch killer.
        _write(
            os.path.join(self.root, "bot", "handlers", "bad.py"),
            """
            from aiogram import F, Router
            router = Router()

            @router.message(F.text)
            async def catch_all_text(message, state):
                if await state.get_state() != "Foo.bar":
                    return
            """,
        )
        # OUT OF SCOPE: same anti-pattern but not under bot/handlers/.
        _write(
            os.path.join(self.root, "services", "boom.py"),
            """
            from aiogram import F, Router
            router = Router()

            @router.message(F.text)
            async def out_of_scope(message, state):
                pass
            """,
        )
        self.engine = QueryEngine(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_list_includes_registered_rule(self) -> None:
        self.assertIn("aiogram-state-check-in-body", self.engine.list_anti_patterns())

    def test_flags_bare_f_text_in_handlers(self) -> None:
        hits = self.engine.find_anti_patterns("aiogram-state-check-in-body")
        functions = {h["function"] for h in hits}
        self.assertIn("catch_all_text", functions)
        # Handler with state filter is NOT flagged.
        self.assertNotIn("take_user_id", functions)
        # Out-of-scope file (not under bot/handlers/) is ignored.
        self.assertNotIn("out_of_scope", functions)

    def test_record_shape(self) -> None:
        hits = self.engine.find_anti_patterns("aiogram-state-check-in-body")
        rec = next(h for h in hits if h["function"] == "catch_all_text")
        self.assertEqual(rec["rule"], "aiogram-state-check-in-body")
        self.assertEqual(rec["file"], "bot/handlers/bad.py")
        self.assertIsInstance(rec["line"], int)
        self.assertIn("F.", rec["evidence"])

    def test_unknown_rule_returns_empty(self) -> None:
        self.assertEqual(self.engine.find_anti_patterns("does-not-exist"), [])


if __name__ == "__main__":
    unittest.main()
