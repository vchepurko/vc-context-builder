"""Tests for logline_to_symbol — generic Python logging-line parser."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from logline_parser import logline_to_symbol
from mcp_server import _tool_specs


def _write(path: str, body: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


class _Fixture:
    def __init__(self) -> None:
        self.root = tempfile.mkdtemp(prefix="logline_")
        # Create a fake project module that the dotted logger name
        # in our test lines should map to.
        _write(
            os.path.join(self.root, "myproj", "bot", "handlers", "admin_staff.py"),
            "def adm_staff_add():\n    pass\n",
        )

    def cleanup(self) -> None:
        for cur, dirs, files in os.walk(self.root, topdown=False):
            for f in files:
                os.remove(os.path.join(cur, f))
            for d in dirs:
                os.rmdir(os.path.join(cur, d))
        os.rmdir(self.root)


class TestLogLineParse(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_canonical_line_maps_logger_to_file(self) -> None:
        line = (
            "2026-05-04 19:46:55,338 [INFO] myproj.bot.handlers.admin_staff: "
            "adm_staff_add fired: user=255639679"
        )
        out = logline_to_symbol(self.fx.root, line)
        self.assertTrue(out["matched"])
        self.assertEqual(out["level"], "INFO")
        self.assertEqual(out["logger"], "myproj.bot.handlers.admin_staff")
        self.assertEqual(out["file"], "myproj/bot/handlers/admin_staff.py")
        self.assertIn("adm_staff_add fired", out["message"])
        self.assertEqual(out["timestamp"], "2026-05-04 19:46:55,338")
        # No symbols dict passed — only the hint comes through.
        self.assertEqual(out.get("symbol_hint"), "adm_staff_add")
        self.assertNotIn("symbol", out)

    def test_unknown_logger_returns_no_file(self) -> None:
        line = "[INFO] unknown.module: did something"
        out = logline_to_symbol(self.fx.root, line)
        self.assertTrue(out["matched"])
        self.assertIsNone(out["file"])

    def test_symbol_resolution_when_symbols_provided(self) -> None:
        symbols = {
            "adm_staff_add": {
                "file": "myproj/bot/handlers/admin_staff.py",
                "role": "callback-handler",
            },
        }
        line = "[INFO] myproj.bot.handlers.admin_staff: adm_staff_add fired: ..."
        out = logline_to_symbol(self.fx.root, line, symbols=symbols)
        self.assertEqual(out["symbol"], "adm_staff_add")
        self.assertEqual(out["symbol_file"], "myproj/bot/handlers/admin_staff.py")
        self.assertEqual(out["role"], "callback-handler")
        self.assertNotIn("symbol_hint", out)

    def test_unrecognised_format_returns_matched_false(self) -> None:
        # Uvicorn-default format — no logger name we can parse.
        line = "INFO:     Started server process [62]"
        out = logline_to_symbol(self.fx.root, line)
        self.assertFalse(out["matched"])

    def test_line_without_timestamp_still_parses(self) -> None:
        line = "[ERROR] myproj.bot.handlers.admin_staff: something broke"
        out = logline_to_symbol(self.fx.root, line)
        self.assertTrue(out["matched"])
        self.assertEqual(out["level"], "ERROR")

    def test_empty_input_safe(self) -> None:
        self.assertEqual(logline_to_symbol(self.fx.root, ""), {"matched": False, "raw": ""})


class TestMcpToolWiring(unittest.TestCase):
    def test_logline_to_symbol_listed(self) -> None:
        names = {spec["name"] for spec in _tool_specs()}
        self.assertIn("logline_to_symbol", names)

    def test_line_required(self) -> None:
        spec = next(s for s in _tool_specs() if s["name"] == "logline_to_symbol")
        self.assertEqual(spec["inputSchema"].get("required", []), ["line"])


if __name__ == "__main__":
    unittest.main()
