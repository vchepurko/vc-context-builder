"""Tests for the role-detection helpers in `symbols.py`.

The helpers map AST nodes / file paths to short role tags
(`route`, `webhook`, `aiogram-handler`, `migration`, `repository`,
`service`, `api-client`, ...) — agents query by role via
`find_by_role`. If the heuristics drift, the role index drifts with
them, so tests pin the contract.
"""

from __future__ import annotations

import ast
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from symbols import (
    extract_decorator_roles,
    extract_scheduler_jobs_from_codebase,
    is_states_group_class,
    is_webhook_function,
    path_role,
)


def _first_def(src: str) -> ast.AST:
    """Parse the snippet and return its first top-level statement."""
    return ast.parse(src).body[0]


class ExtractDecoratorRolesTests(unittest.TestCase):
    def test_fastapi_route(self) -> None:
        node = _first_def("@router.get('/x')\nasync def handler(): pass\n")
        self.assertEqual(extract_decorator_roles(node), "route")

    def test_aiogram_command(self) -> None:
        node = _first_def("@router.message(Command('start'))\nasync def cmd(): pass\n")
        self.assertEqual(extract_decorator_roles(node), "command-handler")

    def test_aiogram_fsm_message(self) -> None:
        node = _first_def("@router.message(MyState.waiting)\nasync def fsm_handler(): pass\n")
        self.assertEqual(extract_decorator_roles(node), "fsm-message-handler")

    def test_aiogram_text_match(self) -> None:
        node = _first_def("@router.message(F.text == 'hi')\nasync def text_handler(): pass\n")
        self.assertEqual(extract_decorator_roles(node), "text-match-handler")

    def test_aiogram_callback_query(self) -> None:
        node = _first_def("@router.callback_query(F.data == 'x')\nasync def cb(): pass\n")
        self.assertEqual(extract_decorator_roles(node), "callback-handler")

    def test_no_decorators_no_role(self) -> None:
        node = _first_def("def plain(): pass\n")
        self.assertIsNone(extract_decorator_roles(node))

    def test_unknown_decorator_no_role(self) -> None:
        node = _first_def("@some.other.decorator\ndef x(): pass\n")
        self.assertIsNone(extract_decorator_roles(node))


class IsStatesGroupClassTests(unittest.TestCase):
    def test_direct_states_group(self) -> None:
        node = _first_def("class Flow(StatesGroup): pass\n")
        self.assertTrue(is_states_group_class(node))

    def test_attribute_states_group(self) -> None:
        node = _first_def("class Flow(aiogram.fsm.state.StatesGroup): pass\n")
        self.assertTrue(is_states_group_class(node))

    def test_unrelated_class(self) -> None:
        node = _first_def("class Foo(BaseModel): pass\n")
        self.assertFalse(is_states_group_class(node))

    def test_function_not_class(self) -> None:
        node = _first_def("def f(): pass\n")
        self.assertFalse(is_states_group_class(node))


class IsWebhookFunctionTests(unittest.TestCase):
    def test_name_plus_request_param(self) -> None:
        node = _first_def("async def my_webhook(request: Request): pass\n")
        self.assertTrue(is_webhook_function(node))

    def test_callback_in_name(self) -> None:
        node = _first_def("async def payment_callback(request): pass\n")
        self.assertTrue(is_webhook_function(node))

    def test_name_without_request_param(self) -> None:
        # Name says webhook but no `request` arg → not flagged.
        node = _first_def("async def webhook_handler(other): pass\n")
        self.assertFalse(is_webhook_function(node))

    def test_request_param_without_name(self) -> None:
        # Request param but name doesn't hint webhook → not flagged.
        node = _first_def("async def handler(request: Request): pass\n")
        self.assertFalse(is_webhook_function(node))


class PathRoleTests(unittest.TestCase):
    def test_migration(self) -> None:
        self.assertEqual(path_role("alembic/versions/abc.py"), "migration")

    def test_repository(self) -> None:
        self.assertEqual(
            path_role("database/repositories/user.py"),
            "repository",
        )

    def test_service(self) -> None:
        self.assertEqual(path_role("services/auth_service.py"), "service")

    def test_nested_service(self) -> None:
        self.assertEqual(
            path_role("backend/services/billing.py"),
            "service",
        )

    def test_api_client(self) -> None:
        self.assertEqual(
            path_role("bot/api_client/staff.py"),
            "api-client",
        )

    def test_unknown_path_returns_none(self) -> None:
        self.assertIsNone(path_role("misc/random.py"))

    def test_non_python_returns_none(self) -> None:
        self.assertIsNone(path_role("alembic/versions/abc.sql"))


class SchedulerJobsScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-sched-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _write(self, rel: str, content: str) -> None:
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_finds_positional_arg_callable(self) -> None:
        self._write(
            "tasks.py",
            "def my_job(): pass\nscheduler.add_job(my_job, 'interval', minutes=5)\n",
        )
        names = extract_scheduler_jobs_from_codebase(self.root)
        self.assertIn("my_job", names)

    def test_finds_attribute_callable(self) -> None:
        self._write(
            "tasks.py",
            "scheduler.add_job(jobs.run_cleanup, 'cron')\n",
        )
        names = extract_scheduler_jobs_from_codebase(self.root)
        self.assertIn("run_cleanup", names)

    def test_finds_func_kwarg(self) -> None:
        self._write(
            "tasks.py",
            "scheduler.add_job(func=my_kwarg_job, trigger='interval')\n",
        )
        names = extract_scheduler_jobs_from_codebase(self.root)
        self.assertIn("my_kwarg_job", names)

    def test_ignores_string_first_arg(self) -> None:
        # `add_job("name_string", ...)` — not a callable, must skip.
        self._write(
            "tasks.py",
            "scheduler.add_job('not_a_callable', 'interval')\n",
        )
        names = extract_scheduler_jobs_from_codebase(self.root)
        self.assertEqual(names, set())


if __name__ == "__main__":
    unittest.main()
