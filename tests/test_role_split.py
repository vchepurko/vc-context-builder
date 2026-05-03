"""Tests for the aiogram-handler role split.

Verifies that the parser tags handlers with the most specific subrole
(``callback-handler``, ``command-handler``, ``fsm-message-handler``,
``text-match-handler``, ``catch-all-handler``) and that ``StatesGroup``
classes get a dedicated ``fsm-state`` role. Also pins down the
backward-compatible behaviour of ``find_by_role("aiogram-handler")``.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from parsers.python_parser import PythonParser  # noqa: E402
from query_engine import QueryEngine  # noqa: E402


class _TmpFile:
    """Tiny RAII helper — writes a file in cwd and removes it on exit."""

    def __init__(self, name: str, body: str) -> None:
        self.name = name
        self.body = textwrap.dedent(body)

    def __enter__(self) -> str:
        with open(self.name, "w", encoding="utf-8") as fh:
            fh.write(self.body)
        return self.name

    def __exit__(self, *exc) -> None:
        if os.path.exists(self.name):
            os.remove(self.name)


def _roles(parser: PythonParser, name: str) -> dict:
    """Return ``{export_name: role}`` from a single-file parse."""
    data = parser.extract(name)
    return {e["name"]: e.get("role") for e in data["exports"]}


class TestAiogramRoleSplit(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = PythonParser()

    def test_callback_query_decorator(self) -> None:
        with _TmpFile("dummy_role_cb.py", """
            from aiogram import F, Router
            router = Router()

            @router.callback_query(F.data == "adm:staff_add")
            async def adm_staff_add(callback): ...
        """) as fname:
            roles = _roles(self.parser, fname)
        self.assertEqual(roles["adm_staff_add"], "callback-handler")

    def test_message_with_command_filter(self) -> None:
        with _TmpFile("dummy_role_cmd.py", """
            from aiogram import Router
            from aiogram.filters import Command
            router = Router()

            @router.message(Command("start"))
            async def cmd_start(msg): ...
        """) as fname:
            roles = _roles(self.parser, fname)
        self.assertEqual(roles["cmd_start"], "command-handler")

    def test_message_with_fsm_state_filter(self) -> None:
        with _TmpFile("dummy_role_fsm.py", """
            from aiogram import F, Router
            router = Router()

            @router.message(AddStaffState.waiting_user_id, F.text)
            async def staff_id_input(msg, state): ...
        """) as fname:
            roles = _roles(self.parser, fname)
        # FSM detection wins over ``F.text`` because the state ref is
        # the more specific signal.
        self.assertEqual(roles["staff_id_input"], "fsm-message-handler")

    def test_message_with_text_filter_only(self) -> None:
        with _TmpFile("dummy_role_text.py", """
            from aiogram import F, Router
            router = Router()

            @router.message(F.text == "/help")
            async def help_handler(msg): ...
        """) as fname:
            roles = _roles(self.parser, fname)
        self.assertEqual(roles["help_handler"], "text-match-handler")

    def test_bare_message_decorator_is_catch_all(self) -> None:
        with _TmpFile("dummy_role_catchall.py", """
            from aiogram import Router
            router = Router()

            @router.message()
            async def fallback(msg): ...
        """) as fname:
            roles = _roles(self.parser, fname)
        self.assertEqual(roles["fallback"], "catch-all-handler")

    def test_unrecognised_filter_falls_back_to_aiogram_handler(self) -> None:
        with _TmpFile("dummy_role_fallback.py", """
            from aiogram import Router
            router = Router()
            some_filter = lambda *a, **kw: True

            @router.message(some_filter)
            async def weird(msg): ...
        """) as fname:
            roles = _roles(self.parser, fname)
        self.assertEqual(roles["weird"], "aiogram-handler")

    def test_other_aiogram_events_keep_umbrella(self) -> None:
        with _TmpFile("dummy_role_other.py", """
            from aiogram import Router
            router = Router()

            @router.edited_message()
            async def on_edited(msg): ...
        """) as fname:
            roles = _roles(self.parser, fname)
        self.assertEqual(roles["on_edited"], "aiogram-handler")

    def test_states_group_class_gets_fsm_state_role(self) -> None:
        with _TmpFile("dummy_role_states.py", """
            from aiogram.fsm.state import State, StatesGroup

            class AddStaffState(StatesGroup):
                waiting_user_id = State()

            class JustAClass:
                pass
        """) as fname:
            roles = _roles(self.parser, fname)
        self.assertEqual(roles["AddStaffState"], "fsm-state")
        self.assertIsNone(roles["JustAClass"])

    def test_route_decorator_unchanged(self) -> None:
        # Double-check we didn't accidentally regress FastAPI tagging
        # while reorganising the aiogram path.
        with _TmpFile("dummy_role_route.py", """
            from fastapi import APIRouter
            router = APIRouter()

            @router.get("/api/foo")
            async def get_foo(): ...
        """) as fname:
            roles = _roles(self.parser, fname)
        self.assertEqual(roles["get_foo"], "route")


class TestUmbrellaQueryCompat(unittest.TestCase):
    """``find_by_role("aiogram-handler")`` and ``list_roles()`` keep
    behaving sanely after the split.

    Builds a synthetic ``agent_root.json`` in a tmp directory rather
    than relying on the parent project's index, so the test stays
    isolated.
    """

    def setUp(self) -> None:
        self.tmpdir = os.path.abspath("dummy_role_query_root")
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(os.path.join(self.tmpdir, "agent_root.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "project_root": self.tmpdir,
                "modules": [],
                "roles": {
                    "callback-handler": ["a", "b"],
                    "command-handler": ["b", "c"],   # overlap on `b`
                    "fsm-message-handler": ["d"],
                    "text-match-handler": ["e"],
                    "catch-all-handler": ["f"],
                    "aiogram-handler": ["g"],         # legacy umbrella for non-message events
                    "fsm-state": ["AddStaffState"],
                    "route": ["get_foo"],
                },
            }, fh)
            fh.write("\n")
        self.engine = QueryEngine(self.tmpdir)

    def tearDown(self) -> None:
        path = os.path.join(self.tmpdir, "agent_root.json")
        if os.path.exists(path):
            os.remove(path)
        try:
            os.rmdir(self.tmpdir)
        except OSError:
            pass

    def test_umbrella_unions_subroles_dedup_sorted(self) -> None:
        names = self.engine.find_by_role("aiogram-handler")
        self.assertEqual(names, sorted(set(["a", "b", "c", "d", "e", "f", "g"])))

    def test_subrole_query_still_works(self) -> None:
        self.assertEqual(self.engine.find_by_role("callback-handler"), ["a", "b"])
        self.assertEqual(self.engine.find_by_role("fsm-state"), ["AddStaffState"])

    def test_list_roles_includes_synthetic_umbrella_count(self) -> None:
        counts = self.engine.list_roles()
        self.assertEqual(counts["callback-handler"], 2)
        self.assertEqual(counts["fsm-state"], 1)
        # Synthetic umbrella: union of all subrole members + the legacy
        # ``aiogram-handler`` bucket itself, deduped → 7.
        self.assertEqual(counts["aiogram-handler"], 7)


if __name__ == "__main__":
    unittest.main()
