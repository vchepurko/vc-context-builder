"""Smoke tests for the stdio MCP server.

We spawn ``mcp_server.py`` as a subprocess, feed line-delimited
JSON-RPC frames, and parse the responses.

The tool registry is verified two ways:

1. **Parity test** — ``_tool_specs()`` (the JSON-Schema list returned
   over the wire) and ``_Dispatcher._handlers`` (what actually runs)
   must agree on the set of tool names. Without this, you can ship a
   spec for a tool whose handler is missing (or vice versa).
2. **Snapshot test** — names are pinned to ``tests/fixtures/tools_list
   .json``. Adding a tool without updating the fixture fails CI; the
   message points at ``python3 tests/regen_snapshots.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

if __package__ is None or __package__ == "":
    from test_query_engine import FixtureMixin  # type: ignore[import-not-found]
else:
    from .test_query_engine import FixtureMixin  # type: ignore[import-not-found]


_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
_SERVER = os.path.join(_SUBMODULE, "mcp_server.py")
_FIXTURE = os.path.join(_HERE, "fixtures", "tools_list.json")


def _drive(root: str, requests: list) -> list:
    """Send each request as a JSON line, return the parsed responses."""
    payload = "\n".join(json.dumps(r) for r in requests) + "\n"
    proc = subprocess.run(
        [sys.executable, _SERVER, "--root", root],
        input=payload,
        capture_output=True,
        text=True,
        timeout=15,
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
        responses = _drive(
            self.root,
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            ],
        )
        self.assertEqual(len(responses), 1)
        result = responses[0]["result"]
        self.assertEqual(result["serverInfo"]["name"], "vc-context")
        self.assertIn("tools", result["capabilities"])

    def test_tools_list_matches_snapshot(self) -> None:
        """The set of tool names returned over the wire must match the
        committed snapshot fixture. Adding/removing a tool without
        updating the snapshot fails here on purpose — run
        ``python3 tests/regen_snapshots.py`` to sync after a
        deliberate change."""
        responses = _drive(
            self.root,
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            ],
        )
        self.assertEqual(len(responses), 2)
        names = sorted(t["name"] for t in responses[1]["result"]["tools"])
        with open(_FIXTURE, encoding="utf-8") as fh:
            expected = json.load(fh)
        self.assertEqual(
            names,
            expected,
            (
                "tools/list drifted from snapshot.  "
                "If the change is intentional, regenerate via:\n"
                "    python3 tests/regen_snapshots.py"
            ),
        )

    def test_tools_list_spec_dispatcher_parity(self) -> None:
        """Every tool in ``_tool_specs()`` must have a handler in
        ``_Dispatcher._handlers`` and vice versa. Drift here ships a
        broken server — either a documented tool that errors with
        ``Unknown tool`` or a hidden handler invisible to clients."""
        sys.path.insert(0, _SUBMODULE)
        try:
            from mcp_server import _Dispatcher, _tool_specs  # type: ignore[import-not-found]
            from query_engine import QueryEngine  # type: ignore[import-not-found]
        finally:
            if _SUBMODULE in sys.path:
                sys.path.remove(_SUBMODULE)
        spec_names = {t["name"] for t in _tool_specs()}
        dispatcher_names = set(_Dispatcher(QueryEngine(self.root))._handlers.keys())
        self.assertEqual(
            spec_names,
            dispatcher_names,
            "Spec and dispatcher disagree.  "
            f"Only in spec: {sorted(spec_names - dispatcher_names)}.  "
            f"Only in dispatcher: {sorted(dispatcher_names - spec_names)}.",
        )

    def test_tools_call_find_symbol(self) -> None:
        responses = _drive(
            self.root,
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "find_symbol",
                        "arguments": {"name": "liqpay_callback"},
                    },
                },
            ],
        )
        self.assertEqual(len(responses), 2)
        content = responses[1]["result"]["content"]
        self.assertEqual(content[0]["type"], "text")
        payload = json.loads(content[0]["text"])
        self.assertEqual(payload["file"], "pkg_a/webhooks.py")

    def test_unknown_method_returns_error(self) -> None:
        responses = _drive(
            self.root,
            [
                {"jsonrpc": "2.0", "id": 1, "method": "no_such_method", "params": {}},
            ],
        )
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
