"""Stdlib-only MCP server over stdio for the vc-context query engine.

Implements a minimal subset of the Model Context Protocol JSON-RPC 2.0
surface: ``initialize``, ``tools/list``, ``tools/call`` (and a tolerant
no-op for ``notifications/*``). Six tools, one per ``QueryEngine``
method.

Why hand-rolled? The submodule's contract is "zero third-party deps,
stdlib only", and the MCP wire protocol is small enough that an SDK
would add more surface area than the implementation itself.

Reference: https://spec.modelcontextprotocol.io/specification/

Frame format on stdio: line-delimited JSON (one JSON object per line).
This matches the line-delimited transport that Claude Code, Cursor,
and Codex CLI all default to for stdio servers.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from query_engine import QueryEngine  # noqa: E402


SERVER_NAME = "vc-context"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


# ----------------------------------------------------------------------
# Tool registry
# ----------------------------------------------------------------------

def _tool_specs() -> List[Dict[str, Any]]:
    """JSON-Schema descriptors for the six exposed tools."""
    return [
        {
            "name": "find_symbol",
            "description": (
                "Look up a symbol in agent_symbols.json. Returns the "
                "{file, kind, params, doc, role} record, or null when "
                "the name is unknown."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name (case-sensitive).",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "find_by_role",
            "description": (
                "Return every symbol name tagged with the given role "
                "(e.g. 'webhook', 'route', 'migration', "
                "'scheduler-job', 'repository', 'service', 'api-client', "
                "'aiogram-handler')."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                },
                "required": ["role"],
            },
        },
        {
            "name": "who_calls",
            "description": (
                "Best-effort reverse-dependency lookup: return files "
                "that import the package containing this symbol or "
                "list the symbol name in their dependencies. Heuristic, "
                "not a true call graph — confirm by reading the source."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                },
                "required": ["symbol"],
            },
        },
        {
            "name": "summarise_module",
            "description": (
                "Tight summary of a folder's _module_map.json: file "
                "names + each export's name/kind/role/first-line doc. "
                "Params are stripped — call find_symbol for signatures."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "Project-relative folder path.",
                    },
                },
                "required": ["folder"],
            },
        },
        {
            "name": "list_roles",
            "description": "Return a {role: count} map across the project.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "list_modules",
            "description": "Return every scanned module folder.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "lint_violations",
            "description": (
                "Run the convention linter and return all violations. "
                "Rules live in .vc-context/conventions.json at the parent "
                "project root. Empty list when the file is missing."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "find_test",
            "description": (
                "Return the nearest existing test for a symbol "
                "(test_file, test_function, line) or null. Reads "
                "agent_tests.json with a live-scan fallback."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        },
        {
            "name": "route_callers",
            "description": (
                "Return the JS/TS call-sites that hit a backend route "
                "path (e.g. '/api/foo' or 'GET /api/foo')."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "route_for_js_call",
            "description": (
                "Return every backend route whose callers_js list "
                "mentions the given JS/TS file path."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        },
    ]


# ----------------------------------------------------------------------
# Tool dispatch
# ----------------------------------------------------------------------

class _Dispatcher:
    """Glue between MCP tool calls and the ``QueryEngine``."""

    def __init__(self, engine: QueryEngine) -> None:
        self.engine = engine
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "find_symbol":       self._find_symbol,
            "find_by_role":      self._find_by_role,
            "who_calls":         self._who_calls,
            "summarise_module":  self._summarise_module,
            "list_roles":        self._list_roles,
            "list_modules":      self._list_modules,
            "lint_violations":   self._lint_violations,
            "find_test":         self._find_test,
            "route_callers":     self._route_callers,
            "route_for_js_call": self._route_for_js_call,
        }

    def call(self, name: str, args: Dict[str, Any]) -> Any:
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")
        return handler(args or {})

    def _find_symbol(self, args: Dict[str, Any]) -> Any:
        return self.engine.find_symbol(str(args.get("name", "")))

    def _find_by_role(self, args: Dict[str, Any]) -> Any:
        return self.engine.find_by_role(str(args.get("role", "")))

    def _who_calls(self, args: Dict[str, Any]) -> Any:
        return self.engine.who_calls(str(args.get("symbol", "")))

    def _summarise_module(self, args: Dict[str, Any]) -> Any:
        return self.engine.summarise_module(str(args.get("folder", "")))

    def _list_roles(self, args: Dict[str, Any]) -> Any:
        return self.engine.list_roles()

    def _list_modules(self, args: Dict[str, Any]) -> Any:
        return self.engine.list_modules()

    def _lint_violations(self, args: Dict[str, Any]) -> Any:
        return self.engine.lint_violations()

    def _find_test(self, args: Dict[str, Any]) -> Any:
        return self.engine.find_test(str(args.get("symbol", "")))

    def _route_callers(self, args: Dict[str, Any]) -> Any:
        return self.engine.route_callers(str(args.get("path", "")))

    def _route_for_js_call(self, args: Dict[str, Any]) -> Any:
        return self.engine.route_for_js_call(str(args.get("file_path", "")))


# ----------------------------------------------------------------------
# JSON-RPC framing
# ----------------------------------------------------------------------

def _ok(req_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


def _content_text(payload: Any) -> Dict[str, Any]:
    """Wrap a Python object as MCP tool content (single text block)."""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    return {"content": [{"type": "text", "text": text}]}


# ----------------------------------------------------------------------
# Method handlers
# ----------------------------------------------------------------------

def handle_request(req: Dict[str, Any], dispatcher: _Dispatcher) -> Optional[Dict[str, Any]]:
    """Translate one JSON-RPC request into a response.

    Returns ``None`` for notifications (requests without an ``id``) —
    the MCP transport expects those to be silent.
    """
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    # Notifications carry no id; reply silently.
    is_notification = "id" not in req

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        return None if is_notification else _ok(req_id, result)

    if method == "tools/list":
        return None if is_notification else _ok(req_id, {"tools": _tool_specs()})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str):
            return _err(req_id, -32602, "Missing tool name")
        try:
            value = dispatcher.call(name, args)
        except FileNotFoundError as exc:
            payload = {"error": "missing_artifact", "detail": str(exc)}
            result = _content_text(payload)
            result["isError"] = True
            return _ok(req_id, result)
        except Exception as exc:  # pragma: no cover — surface as MCP error
            return _err(req_id, -32000, f"Tool failed: {exc}",
                        traceback.format_exc())
        return _ok(req_id, _content_text(value))

    if method in ("ping",):
        return None if is_notification else _ok(req_id, {})

    if isinstance(method, str) and method.startswith("notifications/"):
        # Tolerant no-op for client-initiated notifications.
        return None

    if is_notification:
        return None
    return _err(req_id, -32601, f"Method not found: {method}")


# ----------------------------------------------------------------------
# Stdio loop
# ----------------------------------------------------------------------

def serve(project_root: str, stdin=None, stdout=None) -> int:
    """Read line-delimited JSON-RPC frames forever.

    Uses unbuffered writes so the host process sees each response as
    soon as it's produced.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    engine = QueryEngine(project_root)
    dispatcher = _Dispatcher(engine)

    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            response = _err(None, -32700, f"Parse error: {exc}")
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
            continue

        # Allow batched requests (a JSON array of frames).
        if isinstance(req, list):
            responses = [
                handle_request(item, dispatcher) for item in req
                if isinstance(item, dict)
            ]
            responses = [r for r in responses if r is not None]
            if responses:
                stdout.write(json.dumps(responses) + "\n")
                stdout.flush()
            continue

        if not isinstance(req, dict):
            response = _err(None, -32600, "Invalid request: expected object")
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
            continue

        response = handle_request(req, dispatcher)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    # Accept --root <path> for symmetry with the CLI; default cwd.
    project_root = os.getcwd()
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            project_root = argv[i + 1]
            i += 2
            continue
        i += 1
    return serve(project_root)


if __name__ == "__main__":
    raise SystemExit(main())
