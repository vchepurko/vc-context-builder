"""Smoke tests for the stdio MCP server.

We spawn ``mcp_server.py`` as a subprocess, feed line-delimited
JSON-RPC frames, and parse the responses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from test_query_engine import FixtureMixin  # type: ignore[import-not-found]


_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
_SERVER = os.path.join(_SUBMODULE, "mcp_server.py")


def _drive(root: str, requests: list) -> list:
    """Send each request as a JSON line, return the parsed responses."""
    payload = "\n".join(json.dumps(r) for r in requests) + "\n"
    proc = subprocess.run(
        [sys.executable, _SERVER, "--root", root],
        input=payload, capture_output=True, text=True, timeout=15,
    )
    if proc.returncode != 0:
        raise AssertionError(f"server exited {proc.returncode}: {proc.stderr}")
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


class McpServerTests(FixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_fixture()

    def test_initialize_returns_server_info(self) -> None:
        responses = _drive(self.root, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        ])
        self.assertEqual(len(responses), 1)
        result = responses[0]["result"]
        self.assertEqual(result["serverInfo"]["name"], "vc-context")
        self.assertIn("tools", result["capabilities"])

    def test_tools_list_exposes_all_tools(self) -> None:
        responses = _drive(self.root, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ])
        self.assertEqual(len(responses), 2)
        tools = responses[1]["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertEqual(names, {
            # Original six.
            "find_symbol", "find_by_role", "who_calls",
            "summarise_module", "list_roles", "list_modules",
            # Feature A / B / C additions.
            "lint_violations", "find_test",
            "route_callers", "route_for_js_call",
            # Feature D — aiogram callback_data resolver.
            "find_callback",
            # Feature F — aiogram FSM flow graph.
            "trace_fsm_flow",
            # Feature G — coverage view by role.
            "coverage_for_role",
            # Feature H — test categorisation (unit / integration).
            "classify_tests",
            "tests_by_category",
            # Feature I — generic call-site lookup + log-line resolver.
            "find_call_sites",
            "logline_to_symbol",
        })

    def test_tools_call_find_symbol(self) -> None:
        responses = _drive(self.root, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {
                    "name": "find_symbol",
                    "arguments": {"name": "liqpay_callback"},
                },
            },
        ])
        self.assertEqual(len(responses), 2)
        content = responses[1]["result"]["content"]
        self.assertEqual(content[0]["type"], "text")
        payload = json.loads(content[0]["text"])
        self.assertEqual(payload["file"], "pkg_a/webhooks.py")

    def test_unknown_method_returns_error(self) -> None:
        responses = _drive(self.root, [
            {"jsonrpc": "2.0", "id": 1, "method": "no_such_method", "params": {}},
        ])
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
