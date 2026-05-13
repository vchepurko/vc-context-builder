"""Integration test for `ContextBuilder` (the orchestration layer).

Unit-testing every method against mocks would just shadow the
implementation. Instead: spin up a tiny synthetic project on disk,
run `ContextBuilder.run()`, and assert the artefacts that fell out.
That's the contract real users observe — if it holds for a 3-file
project, the bigger paths (caching, role aggregation, custom roles)
exercise the same machinery.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from agent_map import ContextBuilder


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class _ProjectFixture(unittest.TestCase):
    """Build a minimal but realistic project tree under a tempdir.

    Layout::

        /<root>
          bot/
            handlers/
              admin.py        # @router.message — aiogram-handler role
            api_client/
              staff.py        # path role: api-client
          services/
            background.py     # path role: service
          tests/
            test_admin.py
    """

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-am-")
        self.addCleanup(shutil.rmtree, self.root, True)

        _write(
            os.path.join(self.root, "bot/handlers/admin.py"),
            (
                "from aiogram import Router\n"
                "router = Router()\n"
                "\n"
                "@router.message(Command('admin'))\n"
                "async def cmd_admin(message):\n"
                "    return 1\n"
            ),
        )
        _write(
            os.path.join(self.root, "bot/api_client/staff.py"),
            ("async def add_admin(user_id: int) -> None:\n    raise ValueError('demo')\n"),
        )
        _write(os.path.join(self.root, "services/background.py"), ("def spawn(coro):\n    pass\n"))
        _write(
            os.path.join(self.root, "tests/test_admin.py"),
            (
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_cmd_admin(self):\n"
                "        pass\n"
            ),
        )


class ContextBuilderRunTests(_ProjectFixture):
    def test_run_emits_required_artefacts(self) -> None:
        builder = ContextBuilder(self.root)
        builder.run()
        for fname in (
            "agent_root.json",
            "agent_symbols.json",
            "agent_tests.json",
            "agent_callbacks.json",
            "agent_fsm_flows.json",
            "agent_test_categories.json",
        ):
            from paths import index_path

            self.assertTrue(
                os.path.exists(index_path(self.root, fname)),
                f"missing artefact: {fname}",
            )

    def test_symbols_carry_line_and_role(self) -> None:
        from paths import index_path

        ContextBuilder(self.root).run()
        with open(index_path(self.root, "agent_symbols.json")) as fh:
            symbols = json.load(fh)

        # `add_admin` should be present with file/line plus path-role.
        self.assertIn("add_admin", symbols)
        rec = symbols["add_admin"]
        self.assertEqual(rec["file"], "bot/api_client/staff.py")
        self.assertGreaterEqual(rec.get("line", 0), 1)
        self.assertEqual(rec.get("role"), "api-client")

        # `cmd_admin` should be tagged as a command-handler from
        # the `@router.message(Command(...))` decorator.
        self.assertIn("cmd_admin", symbols)
        self.assertEqual(symbols["cmd_admin"].get("role"), "command-handler")

    def test_root_index_lists_modules_and_roles(self) -> None:
        ContextBuilder(self.root).run()
        from paths import index_path

        with open(index_path(self.root, "agent_root.json")) as fh:
            root_data = json.load(fh)
        self.assertIn("modules", root_data)
        # Three indexed module folders (bot/handlers, bot/api_client, services).
        self.assertGreaterEqual(len(root_data["modules"]), 3)
        roles = root_data.get("roles") or {}
        self.assertIn("api-client", roles)
        self.assertIn("add_admin", roles["api-client"])

    def test_module_map_per_folder(self) -> None:
        ContextBuilder(self.root).run()
        for folder in ("bot/handlers", "bot/api_client", "services"):
            mp = os.path.join(self.root, folder, "_module_map.json")
            self.assertTrue(os.path.exists(mp), f"missing map: {mp}")
            with open(mp) as fh:
                data = json.load(fh)
            self.assertIn("files", data)


class IgnoreDirsTests(_ProjectFixture):
    """`.git` / `node_modules` and friends MUST stay out of the index."""

    def test_default_ignore_skips_node_modules(self) -> None:
        # Drop a fake node_modules tree — must not show up in symbols.
        _write(
            os.path.join(self.root, "node_modules/pkg/index.py"),
            ("def should_not_be_indexed(): pass\n"),
        )
        ContextBuilder(self.root).run()
        from paths import index_path

        with open(index_path(self.root, "agent_symbols.json")) as fh:
            symbols = json.load(fh)
        self.assertNotIn("should_not_be_indexed", symbols)


if __name__ == "__main__":
    unittest.main()
