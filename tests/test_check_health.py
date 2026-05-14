"""Unit tests for ``check_health`` — the bundled lint+mypy+ruff+format
roll-up.

We don't reach all the way down to subprocess here; the per-tool
inspectors already have their own units. ``check_health`` is a
bundler whose contract is "call all four, return them under
``{lint, mypy, ruff, format}``". So we patch the underlying methods
on the QueryEngine instance and verify the bundling behaviour.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

from query_engine import QueryEngine


class CheckHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = QueryEngine(_SUBMODULE)

    def test_bundles_all_four_with_summary_default(self) -> None:
        with (
            mock.patch.object(self.engine, "lint_violations", return_value=[]) as lint,
            mock.patch.object(self.engine, "mypy_violations", return_value={"total": 0}) as mypy,
            mock.patch.object(self.engine, "ruff_violations", return_value={"total": 0}) as ruff,
            mock.patch.object(self.engine, "ruff_format", return_value={"total": 0}) as fmt,
        ):
            out = self.engine.check_health()

        self.assertEqual(set(out), {"lint", "mypy", "ruff", "format"})
        self.assertEqual(out["lint"], [])
        self.assertEqual(out["mypy"], {"total": 0})
        self.assertEqual(out["ruff"], {"total": 0})
        self.assertEqual(out["format"], {"total": 0})

        lint.assert_called_once_with()
        mypy.assert_called_once_with(summary=True)
        ruff.assert_called_once_with(summary=True)
        fmt.assert_called_once_with(summary=True)

    def test_summary_false_passed_through(self) -> None:
        with (
            mock.patch.object(self.engine, "lint_violations", return_value=[]),
            mock.patch.object(self.engine, "mypy_violations", return_value={}) as mypy,
            mock.patch.object(self.engine, "ruff_violations", return_value={}) as ruff,
            mock.patch.object(self.engine, "ruff_format", return_value={}) as fmt,
        ):
            self.engine.check_health(summary=False)

        mypy.assert_called_once_with(summary=False)
        ruff.assert_called_once_with(summary=False)
        fmt.assert_called_once_with(summary=False)

    def test_dispatcher_wiring(self) -> None:
        """Verify the MCP dispatcher routes ``check_health`` to the
        engine method with kwargs translated from the request args.
        """
        from mcp.dispatcher import Dispatcher

        dispatcher = Dispatcher(self.engine)
        with mock.patch.object(self.engine, "check_health", return_value={"ok": True}) as ch:
            result = dispatcher.call("check_health", {"summary": False})

        self.assertEqual(result, {"ok": True})
        ch.assert_called_once_with(summary=False)

    def test_dispatcher_default_summary_when_unset(self) -> None:
        """Omitting the ``summary`` arg should fall through to the
        engine default (no kwarg passed)."""
        from mcp.dispatcher import Dispatcher

        dispatcher = Dispatcher(self.engine)
        with mock.patch.object(self.engine, "check_health", return_value={}) as ch:
            dispatcher.call("check_health", {})

        ch.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
