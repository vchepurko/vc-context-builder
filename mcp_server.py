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
        {
            "name": "find_callback",
            "description": (
                "Resolve an aiogram callback_data string (e.g. "
                "'adm:staff_add' or 'adm:staff_detail:42') to the "
                "handler(s) that listen for it. Tries an exact match "
                "first, then falls back to the longest startswith "
                "prefix in the index."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"data": {"type": "string"}},
                "required": ["data"],
            },
        },
        {
            "name": "trace_fsm_flow",
            "description": (
                "Trace an aiogram FSM state's lifecycle: where it's "
                "declared, which handlers ENTER it via state.set_state, "
                "and which handlers CONSUME it via decorator filter. "
                "Accepts 'AddStaffState.waiting_user_id' or just "
                "'waiting_user_id' when unambiguous."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"state": {"type": "string"}},
                "required": ["state"],
            },
        },
        {
            "name": "coverage_for_role",
            "description": (
                "Test-coverage view by role. Without 'role' — returns "
                "overall + per-role counts and percentages. With 'role' "
                "(any built-in or custom role, including legacy "
                "umbrellas like 'aiogram-handler') — returns "
                "{total, with_test, coverage_pct, missing, covered} "
                "where 'missing' lists symbols WITHOUT a linked test."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": (
                            "Optional role name. Omit for whole-project "
                            "summary."
                        ),
                    },
                },
            },
        },
        {
            "name": "classify_tests",
            "description": (
                "Categorise every test_*.py file as 'unit', "
                "'integration' (touches HTTP/DB/queue boundary OR "
                "carries pytest.mark.integration), or 'unknown'. "
                "Returns {summary: {category: count}, files: "
                "{path: {category, signals}}}. Use to find slow tests "
                "you can defer behind a marker."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "tests_by_category",
            "description": (
                "Return the list of test file paths for a given "
                "category ('unit' / 'integration' / 'unknown')."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"category": {"type": "string"}},
                "required": ["category"],
            },
        },
        {
            "name": "find_call_sites",
            "description": (
                "Reverse call-site lookup. Return every Call(...) site "
                "in the project whose target matches a given callable. "
                "Accepts a plain name ('foo') or dotted path ('x.y'). "
                "Optional match_path is an fnmatch-style glob "
                "('services/**', 'bot/handlers/*.py'). Use to find who "
                "calls state.clear / session.commit / cache.delete / etc."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "callable": {"type": "string"},
                    "match_path": {"type": "string"},
                },
                "required": ["callable"],
            },
        },
        {
            "name": "logline_to_symbol",
            "description": (
                "Parse a Python logging line ('YYYY-MM-DD HH:MM:SS "
                "[LEVEL] dotted.logger: message') into "
                "{level, logger, file, message, symbol?, symbol_file?, "
                "role?}. Maps the dotted logger name to the project "
                "file via __name__-convention; if the message starts "
                "with a known symbol, folds in its file/role too."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"line": {"type": "string"}},
                "required": ["line"],
            },
        },
        {
            "name": "list_checks",
            "description": (
                "Return the names of whitelisted commands declared "
                "under .vc-context/conventions.json → 'checks'. Use "
                "before run_check to discover what's safe to invoke "
                "(e.g. 'test-unit', 'lint', 'typecheck')."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "run_check",
            "description": (
                "Execute a whitelisted check declared in "
                ".vc-context/conventions.json. Returns "
                "{returncode, duration_ms, stdout_tail, stderr_tail, "
                "summary, error?}. Unknown name → returncode -2; "
                "timeout → -1; spawn failure → -3. Use to run tests / "
                "lint / typecheck without exposing arbitrary shell."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "timeout_sec": {"type": "integer", "minimum": 1},
                },
                "required": ["name"],
            },
        },
        {
            "name": "inspect_class",
            "description": (
                "Return a structured summary of a Python class — "
                "{file, line, doc, bases, fields, methods}. Looks up "
                "the symbol in agent_symbols.json, then AST-walks the "
                "file. Works for SQLAlchemy models, pydantic schemas, "
                "dataclasses, plain classes. Use instead of `grep` for "
                "'what columns does Admin have?'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "list_locale_keys",
            "description": (
                "Return all i18n keys (sorted), optionally filtered to "
                "one namespace (e.g. 'admin', 'common'). Reads "
                "agent_locale_keys.json, populated for projects with a "
                "locales/<lang>/<ns>.json layout. Empty list when no "
                "locale index is present."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": "Optional: namespace filter (the "
                        "JSON filename without .json).",
                    },
                },
            },
        },
        {
            "name": "find_locale_key",
            "description": (
                "Substring (case-insensitive) match across i18n keys. "
                "Use for 'every key starting with staff_' "
                "(pattern='staff_') or 'all email-related keys' "
                "(pattern='email'). Replaces grep across "
                "locales/*/*.json files."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
        {
            "name": "get_locale_key",
            "description": (
                "Full entry for one i18n key — {namespace, languages, "
                "values: {lang: text}, missing: [langs that own the "
                "namespace file but don't carry this key]}. The "
                "'missing' list is the parity audit hook: empty = "
                "fully translated, non-empty = ship-blocking gap."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                },
                "required": ["key"],
            },
        },
        {
            "name": "notify_log_search",
            "description": (
                "Search the rotating notification audit log emitted "
                "by the project's services/notify pipeline. Returns "
                "matching records as a list of {ts, kind, "
                "recipient_uid, channel, outcome, keys}. AND-combines "
                "filters; empty filters return up to `limit` most-"
                "recent records. Projects without a logs/notify.jsonl "
                "return []. Use this instead of grep'ing log files "
                "for 'did kind X reach user Y?' questions."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "recipient": {"type": "integer"},
                    "channel": {"type": "string", "enum": ["telegram", "email"]},
                    "outcome": {
                        "type": "string",
                        "enum": ["sent", "failed", "skipped"],
                    },
                    "since": {
                        "type": "string",
                        "description": "Relative window like '7d' / '24h' or an "
                                       "ISO date / datetime. None = no cutoff.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Cap response size (default 200) so MCP "
                                       "client doesn't pull megabytes into context.",
                    },
                },
            },
        },
        {
            "name": "notify_log_stats",
            "description": (
                "Aggregate counters over the notification audit log: "
                "{total, by_kind: {kind: {sent, failed, skipped}}, "
                "by_channel: {channel: {sent, failed, skipped}}}. "
                "Optional 'since' (e.g. '7d') trims older records. "
                "Use for 'how is delivery health this week?' "
                "without scanning each record."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "since": {"type": "string"},
                },
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
            "find_callback":     self._find_callback,
            "trace_fsm_flow":    self._trace_fsm_flow,
            "coverage_for_role": self._coverage_for_role,
            "classify_tests":    self._classify_tests,
            "tests_by_category": self._tests_by_category,
            "find_call_sites":   self._find_call_sites,
            "logline_to_symbol": self._logline_to_symbol,
            "list_checks":       self._list_checks,
            "run_check":         self._run_check,
            "inspect_class":     self._inspect_class,
            "list_locale_keys":  self._list_locale_keys,
            "find_locale_key":   self._find_locale_key,
            "get_locale_key":    self._get_locale_key,
            "notify_log_search": self._notify_log_search,
            "notify_log_stats":  self._notify_log_stats,
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

    def _find_callback(self, args: Dict[str, Any]) -> Any:
        return self.engine.find_callback(str(args.get("data", "")))

    def _trace_fsm_flow(self, args: Dict[str, Any]) -> Any:
        return self.engine.trace_fsm_flow(str(args.get("state", "")))

    def _coverage_for_role(self, args: Dict[str, Any]) -> Any:
        # Empty/missing 'role' → whole-project summary; non-empty
        # string → role-scoped detail with missing/covered lists.
        role = args.get("role")
        role_arg = str(role).strip() if isinstance(role, str) and role.strip() else None
        return self.engine.coverage_for_role(role_arg)

    def _classify_tests(self, args: Dict[str, Any]) -> Any:
        return self.engine.classify_tests()

    def _tests_by_category(self, args: Dict[str, Any]) -> Any:
        return self.engine.tests_by_category(str(args.get("category", "")))

    def _find_call_sites(self, args: Dict[str, Any]) -> Any:
        callable_name = str(args.get("callable", "")).strip()
        match_path_raw = args.get("match_path")
        match_path = (
            str(match_path_raw).strip()
            if isinstance(match_path_raw, str) and match_path_raw.strip()
            else None
        )
        return self.engine.find_call_sites(callable_name, match_path)

    def _logline_to_symbol(self, args: Dict[str, Any]) -> Any:
        return self.engine.logline_to_symbol(str(args.get("line", "")))

    def _list_checks(self, args: Dict[str, Any]) -> Any:
        return self.engine.list_checks()

    def _run_check(self, args: Dict[str, Any]) -> Any:
        name = str(args.get("name", "")).strip()
        timeout_raw = args.get("timeout_sec")
        timeout_sec = (
            int(timeout_raw)
            if isinstance(timeout_raw, (int, float)) and timeout_raw > 0
            else None
        )
        return self.engine.run_check(name, timeout_sec=timeout_sec)

    def _inspect_class(self, args: Dict[str, Any]) -> Any:
        return self.engine.inspect_class(str(args.get("name", "")).strip())

    def _list_locale_keys(self, args: Dict[str, Any]) -> Any:
        ns = args.get("namespace")
        ns_str = str(ns).strip() if isinstance(ns, str) and ns.strip() else None
        return self.engine.list_locale_keys(namespace=ns_str)

    def _find_locale_key(self, args: Dict[str, Any]) -> Any:
        return self.engine.find_locale_key(str(args.get("pattern", "")))

    def _get_locale_key(self, args: Dict[str, Any]) -> Any:
        return self.engine.get_locale_key(str(args.get("key", "")).strip())

    def _notify_log_search(self, args: Dict[str, Any]) -> Any:
        kw: Dict[str, Any] = {}
        for name in ("kind", "channel", "outcome", "since"):
            v = args.get(name)
            if isinstance(v, str) and v.strip():
                kw[name] = v.strip()
        if "recipient" in args:
            try:
                kw["recipient"] = int(args["recipient"])
            except (TypeError, ValueError):
                pass
        if "limit" in args:
            try:
                kw["limit"] = max(1, int(args["limit"]))
            except (TypeError, ValueError):
                pass
        return self.engine.notify_log_search(**kw)

    def _notify_log_stats(self, args: Dict[str, Any]) -> Any:
        since = args.get("since")
        if isinstance(since, str) and since.strip():
            return self.engine.notify_log_stats(since=since.strip())
        return self.engine.notify_log_stats()


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
