"""Tests for find_call_sites — generic reverse-call lookup (Feature I)."""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from call_sites import find_call_sites  # noqa: E402
from mcp_server import _tool_specs  # noqa: E402


def _write(path: str, body: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body))


class _Fixture:
    def __init__(self) -> None:
        self.root = tempfile.mkdtemp(prefix="call_sites_")
        _write(os.path.join(self.root, "bot", "h.py"), """
            async def reset(state):
                await state.clear()
                await state.set_state(None)

            async def commit_demo(session):
                session.commit()
        """)
        _write(os.path.join(self.root, "services", "svc.py"), """
            async def soft_reset(state):
                await state.clear()
        """)
        _write(os.path.join(self.root, "lib", "x.py"), """
            def helper(redis):
                redis.delete('key')
        """)
        # File with a syntax error — must be skipped, not crash.
        _write(os.path.join(self.root, "broken.py"), "def x(:\n")

    def cleanup(self) -> None:
        for cur, dirs, files in os.walk(self.root, topdown=False):
            for f in files:
                os.remove(os.path.join(cur, f))
            for d in dirs:
                os.rmdir(os.path.join(cur, d))
        os.rmdir(self.root)


class TestFindCallSites(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_simple_name_finds_all_attribute_chain_endings(self) -> None:
        # ``state.clear`` matches both ``state.clear()`` and ``state.set_state``? No —
        # only `clear` leaf. Caller asked for "clear" alone:
        out = find_call_sites(self.fx.root, "clear")
        files = [r["file"] for r in out]
        self.assertIn("bot/h.py", files)
        self.assertIn("services/svc.py", files)

    def test_dotted_path_matches_full_chain(self) -> None:
        out = find_call_sites(self.fx.root, "state.clear")
        # Both files invoke ``state.clear()`` — both should appear.
        self.assertEqual(len(out), 2)
        self.assertEqual({r["function"] for r in out}, {"reset", "soft_reset"})

    def test_dotted_path_does_not_match_unrelated_pair(self) -> None:
        # ``state.commit`` doesn't exist anywhere — empty result.
        self.assertEqual(find_call_sites(self.fx.root, "state.commit"), [])

    def test_match_path_glob_filters_results(self) -> None:
        out = find_call_sites(self.fx.root, "clear", match_path="services/**")
        files = {r["file"] for r in out}
        self.assertEqual(files, {"services/svc.py"})

    def test_record_has_function_and_raw(self) -> None:
        out = find_call_sites(self.fx.root, "redis.delete")
        self.assertEqual(len(out), 1)
        rec = out[0]
        self.assertEqual(rec["function"], "helper")
        self.assertIn("redis.delete", rec["raw"])
        self.assertGreater(rec["line"], 0)

    def test_empty_callable_returns_empty(self) -> None:
        self.assertEqual(find_call_sites(self.fx.root, ""), [])

    def test_skips_syntax_errors_silently(self) -> None:
        # broken.py exists but mustn't crash the scan.
        out = find_call_sites(self.fx.root, "clear")
        self.assertNotIn("broken.py", [r["file"] for r in out])


class TestMcpToolWiring(unittest.TestCase):
    def test_find_call_sites_listed(self) -> None:
        names = {spec["name"] for spec in _tool_specs()}
        self.assertIn("find_call_sites", names)

    def test_callable_required_match_path_optional(self) -> None:
        spec = next(s for s in _tool_specs() if s["name"] == "find_call_sites")
        self.assertEqual(spec["inputSchema"].get("required", []), ["callable"])
        self.assertIn("match_path", spec["inputSchema"]["properties"])


if __name__ == "__main__":
    unittest.main()
