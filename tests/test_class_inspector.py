"""Tests for the class inspector (Feature L)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from class_inspector import inspect_class  # noqa: E402
from mcp_server import _tool_specs  # noqa: E402
from query_engine import QueryEngine  # noqa: E402


def _write(path: str, body: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body))


class _Fixture:
    def __init__(self) -> None:
        self.root = tempfile.mkdtemp(prefix="cls_inspector_")
        # SQLAlchemy-style class with annotated columns + helper method.
        _write(os.path.join(self.root, "models.py"), '''
            from typing import Optional


            class Admin:
                """Roles: superadmin, manager, seller."""

                __tablename__ = "admins"

                user_id: int = 0
                role: str = "manager"
                name: str = ""
                email: str = ""

                def is_superadmin(self) -> bool:
                    """True when role == 'superadmin'."""
                    return self.role == "superadmin"

                async def soft_delete(self) -> None:
                    pass

                def _hidden(self) -> None:
                    pass


            class Empty:
                pass
        ''')
        # Symbols file pointing at models.py — emulating the
        # agent_symbols.json that the parser would write at scan time.
        symbols = {
            "Admin": {"file": "models.py", "kind": "class"},
            "Empty": {"file": "models.py", "kind": "class"},
            "is_superadmin": {"file": "models.py", "kind": "func"},
        }
        with open(os.path.join(self.root, "agent_symbols.json"), "w", encoding="utf-8") as fh:
            json.dump(symbols, fh)
        self.symbols = symbols

    def cleanup(self) -> None:
        for cur, dirs, files in os.walk(self.root, topdown=False):
            for f in files:
                os.remove(os.path.join(cur, f))
            for d in dirs:
                os.rmdir(os.path.join(cur, d))
        os.rmdir(self.root)


class TestInspectClass(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_full_payload_for_known_class(self) -> None:
        out = inspect_class(self.fx.root, "Admin", symbols=self.fx.symbols)
        self.assertIsNotNone(out)
        self.assertEqual(out["name"], "Admin")
        self.assertEqual(out["file"], "models.py")
        self.assertEqual(out["doc"], "Roles: superadmin, manager, seller.")
        # `Admin` has no explicit base in the fixture
        self.assertEqual(out["bases"], [])

    def test_fields_extracted_with_type_and_default(self) -> None:
        out = inspect_class(self.fx.root, "Admin", symbols=self.fx.symbols)
        names = {f["name"]: f for f in out["fields"]}
        self.assertIn("user_id", names)
        self.assertEqual(names["user_id"]["type"], "int")
        self.assertEqual(names["user_id"]["default"], "0")
        self.assertIn("role", names)
        self.assertEqual(names["role"]["default"], "'manager'")
        # Plain Assign (no annotation) → type=None, default kept.
        self.assertIn("__tablename__", names)
        self.assertIsNone(names["__tablename__"]["type"])
        self.assertEqual(names["__tablename__"]["default"], "'admins'")

    def test_methods_include_async_skip_private(self) -> None:
        out = inspect_class(self.fx.root, "Admin", symbols=self.fx.symbols)
        method_names = {m["name"]: m for m in out["methods"]}
        self.assertIn("is_superadmin", method_names)
        self.assertEqual(method_names["is_superadmin"]["doc"], "True when role == 'superadmin'.")
        self.assertIn("soft_delete", method_names)
        self.assertEqual(method_names["soft_delete"]["kind"], "async-func")
        # _hidden is filtered out
        self.assertNotIn("_hidden", method_names)

    def test_unknown_name_returns_none(self) -> None:
        self.assertIsNone(inspect_class(self.fx.root, "NoSuchClass", symbols=self.fx.symbols))

    def test_non_class_symbol_returns_none(self) -> None:
        # `is_superadmin` is registered with kind="func" → must be ignored
        self.assertIsNone(inspect_class(self.fx.root, "is_superadmin", symbols=self.fx.symbols))

    def test_empty_class_yields_empty_lists(self) -> None:
        out = inspect_class(self.fx.root, "Empty", symbols=self.fx.symbols)
        self.assertEqual(out["fields"], [])
        self.assertEqual(out["methods"], [])

    def test_symbols_missing_means_none(self) -> None:
        # Without a symbols index we don't know which file to open.
        self.assertIsNone(inspect_class(self.fx.root, "Admin", symbols=None))

    def test_empty_input_safe(self) -> None:
        self.assertIsNone(inspect_class(self.fx.root, "", symbols=self.fx.symbols))


class TestQueryEngineWiring(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_engine_round_trip(self) -> None:
        engine = QueryEngine(self.fx.root)
        out = engine.inspect_class("Admin")
        self.assertIsNotNone(out)
        self.assertEqual(out["name"], "Admin")
        self.assertGreater(len(out["fields"]), 0)


class TestMcpToolWiring(unittest.TestCase):
    def test_tool_listed(self) -> None:
        names = {spec["name"] for spec in _tool_specs()}
        self.assertIn("inspect_class", names)

    def test_name_required(self) -> None:
        spec = next(s for s in _tool_specs() if s["name"] == "inspect_class")
        self.assertEqual(spec["inputSchema"].get("required", []), ["name"])


if __name__ == "__main__":
    unittest.main()
