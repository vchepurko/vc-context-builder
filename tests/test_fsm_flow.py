"""Tests for the aiogram FSM flow graph (Feature F).

End-to-end coverage:
* Collecting StatesGroup classes + their fields.
* ``state.set_state(X.y)`` → ``entered_by`` records (with the entering
  callback_data when the handler is a callback_query).
* ``@router.message(X.y, ...)`` / ``@router.callback_query(X.y, ...)``
  → ``consumed_by`` records carrying the auxiliary filter text.
* ``QueryEngine.trace_fsm_flow`` returning the assembled record by
  full or short state name.
* MCP tool registration so the new tool is reachable from agents.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fsm_flow import (
    FSM_FLOW_FILENAME,
    collect_fsm_flow,
    collect_state_groups,
    trace_fsm_flow,
    write_fsm_flow,
)
from mcp_server import _tool_specs
from query_engine import QueryEngine

_HANDLERS_SOURCE = textwrap.dedent("""
    from aiogram import F, Router
    from aiogram.fsm.context import FSMContext
    router = Router()

    @router.callback_query(F.data == "adm:staff_add")
    async def adm_staff_add(callback, state: FSMContext):
        await state.set_state(AddStaffState.waiting_user_id)

    @router.message(AddStaffState.waiting_user_id, F.text)
    async def adm_staff_id_input(message, state: FSMContext):
        await state.clear()
""")

_STATES_SOURCE = textwrap.dedent("""
    from aiogram.fsm.state import State, StatesGroup

    class AddStaffState(StatesGroup):
        waiting_user_id = State()

    class ReportBugState(StatesGroup):
        waiting_message = State()
""")


def _write(path: str, body: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


class _ProjectFixture:
    """Synthetic project tree with one state file and one handlers file."""

    def __init__(self) -> None:
        self.root = tempfile.mkdtemp(prefix="fsm_flow_")
        _write(os.path.join(self.root, "states.py"), _STATES_SOURCE)
        _write(os.path.join(self.root, "handlers.py"), _HANDLERS_SOURCE)

    def cleanup(self) -> None:
        for cur, dirs, files in os.walk(self.root, topdown=False):
            for f in files:
                os.remove(os.path.join(cur, f))
            for d in dirs:
                os.rmdir(os.path.join(cur, d))
        os.rmdir(self.root)


class TestCollectStateGroups(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _ProjectFixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_each_field_indexed_with_full_name(self) -> None:
        groups = collect_state_groups(self.fx.root)
        self.assertIn("AddStaffState.waiting_user_id", groups)
        self.assertIn("ReportBugState.waiting_message", groups)
        self.assertEqual(
            groups["AddStaffState.waiting_user_id"]["state_class"]["file"],
            "states.py",
        )
        self.assertGreater(groups["AddStaffState.waiting_user_id"]["state_class"]["line"], 0)

    def test_skips_private_fields(self) -> None:
        path = os.path.join(self.fx.root, "private_states.py")
        _write(
            path,
            textwrap.dedent("""
            from aiogram.fsm.state import State, StatesGroup

            class PrivateGroup(StatesGroup):
                _internal = State()
                public_field = State()
        """),
        )
        groups = collect_state_groups(self.fx.root)
        self.assertNotIn("PrivateGroup._internal", groups)
        self.assertIn("PrivateGroup.public_field", groups)


class TestCollectFsmFlow(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _ProjectFixture()
        self.flow = collect_fsm_flow(self.fx.root)

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_state_record_has_all_three_sections(self) -> None:
        rec = self.flow["AddStaffState.waiting_user_id"]
        self.assertIn("state_class", rec)
        self.assertIn("entered_by", rec)
        self.assertIn("consumed_by", rec)

    def test_entered_by_carries_originating_callback(self) -> None:
        rec = self.flow["AddStaffState.waiting_user_id"]
        entries = rec["entered_by"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["handler"], "adm_staff_add")
        self.assertEqual(entries[0]["callback"], "adm:staff_add")

    def test_consumed_by_carries_filter_summary(self) -> None:
        rec = self.flow["AddStaffState.waiting_user_id"]
        entries = rec["consumed_by"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["handler"], "adm_staff_id_input")
        self.assertEqual(entries[0]["kind"], "message")
        self.assertEqual(entries[0]["filter"], "F.text")

    def test_unrelated_state_has_empty_lists(self) -> None:
        rec = self.flow["ReportBugState.waiting_message"]
        self.assertEqual(rec["entered_by"], [])
        self.assertEqual(rec["consumed_by"], [])


class TestTraceLookup(unittest.TestCase):
    def setUp(self) -> None:
        self.idx = {
            "AddStaffState.waiting_user_id": {
                "state_class": {"file": "s.py", "line": 1},
                "entered_by": [],
                "consumed_by": [],
            },
            "ReportBugState.waiting_message": {
                "state_class": {"file": "s.py", "line": 5},
                "entered_by": [],
                "consumed_by": [],
            },
            "OtherState.waiting_user_id": {  # collides on short name
                "state_class": {"file": "s.py", "line": 9},
                "entered_by": [],
                "consumed_by": [],
            },
        }

    def test_full_name_match_returns_record(self) -> None:
        out = trace_fsm_flow(self.idx, "AddStaffState.waiting_user_id")
        self.assertIsNotNone(out)
        self.assertEqual(out["state"], "AddStaffState.waiting_user_id")

    def test_unambiguous_short_name_match(self) -> None:
        out = trace_fsm_flow(self.idx, "waiting_message")
        self.assertEqual(out["state"], "ReportBugState.waiting_message")

    def test_ambiguous_short_name_returns_none(self) -> None:
        # "waiting_user_id" matches both AddStaffState and OtherState.
        self.assertIsNone(trace_fsm_flow(self.idx, "waiting_user_id"))

    def test_unknown_state_returns_none(self) -> None:
        self.assertIsNone(trace_fsm_flow(self.idx, "NoSuchState.field"))

    def test_empty_input_returns_none(self) -> None:
        self.assertIsNone(trace_fsm_flow(self.idx, ""))


class TestQueryEngineTrace(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _ProjectFixture()
        idx = collect_fsm_flow(self.fx.root)
        write_fsm_flow(self.fx.root, idx)
        self.engine = QueryEngine(self.fx.root)

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_round_trip_full_name(self) -> None:
        out = self.engine.trace_fsm_flow("AddStaffState.waiting_user_id")
        self.assertIsNotNone(out)
        self.assertEqual(out["entered_by"][0]["callback"], "adm:staff_add")

    def test_missing_index_degrades_gracefully(self) -> None:
        os.remove(os.path.join(self.fx.root, FSM_FLOW_FILENAME))
        engine = QueryEngine(self.fx.root)
        self.assertIsNone(engine.trace_fsm_flow("AddStaffState.waiting_user_id"))


class TestMcpToolRegistration(unittest.TestCase):
    def test_trace_fsm_flow_exposed(self) -> None:
        names = {spec["name"] for spec in _tool_specs()}
        self.assertIn("trace_fsm_flow", names)


if __name__ == "__main__":
    unittest.main()
