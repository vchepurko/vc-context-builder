"""Dispatcher — translates an MCP tool name + JSON args into a call
on the shared :class:`~query_engine.QueryEngine` instance.

Public surface: ``Dispatcher(engine).call(name, args)`` returns the
JSON-serialisable result. Unknown names raise ``ValueError`` so
``handle_request`` in :mod:`mcp.rpc` can convert them to a JSON-RPC
error response.

Adding a tool: drop a method ``_my_tool(args)`` on ``Dispatcher`` and
register it in ``_handlers``. Then add the matching JSON-Schema
record to :func:`mcp.specs.tool_specs`. The parity test guards both
halves staying aligned.

Metrics: when constructed with ``metrics_writer``, every call emits
one JSONL line via :class:`mcp.metrics.MetricsWriter`. The writer
itself never raises into the call site, so the metrics path stays
strictly opt-in / fail-open.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable, Dict, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from mcp.metrics import MetricsWriter
from query_engine import QueryEngine


class Dispatcher:
    """Glue between MCP tool calls and the ``QueryEngine``."""

    def __init__(
        self,
        engine: QueryEngine,
        metrics_writer: Optional[MetricsWriter] = None,
    ) -> None:
        self.engine = engine
        self.metrics_writer = metrics_writer
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "find_symbol": self._find_symbol,
            "find_symbols": self._find_symbols,
            "get_callees": self._get_callees,
            "get_raised_exceptions": self._get_raised_exceptions,
            "verify": self._verify,
            "get_decorated_with": self._get_decorated_with,
            "get_symbol_card": self._get_symbol_card,
            "get_file_card": self._get_file_card,
            "get_changed_symbols": self._get_changed_symbols,
            "repo_map": self._repo_map,
            "read_slice": self._read_slice,
            "find_by_role": self._find_by_role,
            "who_calls": self._who_calls,
            "summarise_module": self._summarise_module,
            "list_roles": self._list_roles,
            "list_modules": self._list_modules,
            "lint_violations": self._lint_violations,
            "find_test": self._find_test,
            "route_callers": self._route_callers,
            "route_for_js_call": self._route_for_js_call,
            "find_callback": self._find_callback,
            "trace_fsm_flow": self._trace_fsm_flow,
            "coverage_for_role": self._coverage_for_role,
            "classify_tests": self._classify_tests,
            "tests_by_category": self._tests_by_category,
            "find_call_sites": self._find_call_sites,
            "logline_to_symbol": self._logline_to_symbol,
            "list_checks": self._list_checks,
            "run_check": self._run_check,
            "get_session_metrics": self._get_session_metrics,
            "inspect_class": self._inspect_class,
            "list_locale_keys": self._list_locale_keys,
            "find_locale_key": self._find_locale_key,
            "find_pattern_in_configs": self._find_pattern_in_configs,
            "list_config_kinds": self._list_config_kinds,
            "rebuild_index": self._rebuild_index,
            "get_locale_key": self._get_locale_key,
            "notify_log_search": self._notify_log_search,
            "notify_log_stats": self._notify_log_stats,
            "ruff_violations": self._ruff_violations,
            "ruff_format": self._ruff_format,
            "mypy_violations": self._mypy_violations,
            "find_in_templates": self._find_in_templates,
            "ng_audit_component": self._ng_audit_component,
            "ng_uses_selector": self._ng_uses_selector,
            "ng_overview": self._ng_overview,
            "ng_inject_graph": self._ng_inject_graph,
            "ng_list_routes": self._ng_list_routes,
            "ng_route_for_path": self._ng_route_for_path,
            "ng_routes_for_component": self._ng_routes_for_component,
        }

    def call(self, name: str, args: Dict[str, Any]) -> Any:
        if self.metrics_writer is None:
            return self._invoke(name, args)
        # Metric path — wrap with timing + best-effort emit. We capture
        # the result/ok in locals so `finally` can record even on raise;
        # the writer's own try/except keeps disk failures invisible.
        t0 = time.monotonic()
        result: Any = None
        ok = False
        try:
            result = self._invoke(name, args)
            ok = True
            return result
        finally:
            t_ms = int((time.monotonic() - t0) * 1000)
            try:
                self.metrics_writer.record(name, args, result, t_ms, ok)
            except Exception:
                pass

    def _invoke(self, name: str, args: Dict[str, Any]) -> Any:
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")
        return handler(args or {})

    def _find_symbol(self, args: Dict[str, Any]) -> Any:
        kw = self._symbol_kwargs(args)
        return self.engine.find_symbol(str(args.get("name", "")), **kw)

    def _find_symbols(self, args: Dict[str, Any]) -> Any:
        names = args.get("names")
        if not isinstance(names, list):
            return {}
        # Defensive cast — clients sometimes pass non-strings.
        cleaned = [str(n) for n in names if n]
        kw = self._symbol_kwargs(args)
        return self.engine.find_symbols(cleaned, **kw)

    @staticmethod
    def _symbol_kwargs(args: Dict[str, Any]) -> Dict[str, Any]:
        """Shared kwarg extractor for find_symbol / find_symbols."""
        kw: Dict[str, Any] = {}
        fields = args.get("fields")
        if isinstance(fields, list):
            kw["fields"] = [str(f) for f in fields if isinstance(f, str)]
        if args.get("include_body") is True:
            kw["include_body"] = True
        return kw

    def _get_callees(self, args: Dict[str, Any]) -> Any:
        return self.engine.get_callees(str(args.get("symbol", "")).strip())

    def _get_raised_exceptions(self, args: Dict[str, Any]) -> Any:
        return self.engine.get_raised_exceptions(str(args.get("symbol", "")).strip())

    def _verify(self, args: Dict[str, Any]) -> Any:
        kind = str(args.get("kind", "")).strip()
        subject = str(args.get("subject", "")).strip()
        target_raw = args.get("target")
        target = (
            str(target_raw).strip() if isinstance(target_raw, str) and target_raw.strip() else None
        )
        return self.engine.verify(kind, subject, target=target)

    def _get_decorated_with(self, args: Dict[str, Any]) -> Any:
        return self.engine.get_decorated_with(str(args.get("decorator", "")).strip())

    def _get_symbol_card(self, args: Dict[str, Any]) -> Any:
        return self.engine.get_symbol_card(str(args.get("symbol", "")).strip())

    def _get_file_card(self, args: Dict[str, Any]) -> Any:
        return self.engine.get_file_card(str(args.get("path", "")).strip())

    def _get_changed_symbols(self, args: Dict[str, Any]) -> Any:
        base = args.get("base")
        base_arg = str(base).strip() if isinstance(base, str) and base.strip() else None
        return self.engine.get_changed_symbols(base=base_arg)

    def _repo_map(self, args: Dict[str, Any]) -> Any:
        return self.engine.repo_map()

    def _read_slice(self, args: Dict[str, Any]) -> Any:
        path = str(args.get("file", "")).strip()
        try:
            start = int(args.get("start", 0))
            end = int(args.get("end", 0))
        except (TypeError, ValueError):
            return None
        if not path or start < 1 or end < start:
            return None
        return self.engine.read_slice(path, start, end)

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
            int(timeout_raw) if isinstance(timeout_raw, (int, float)) and timeout_raw > 0 else None
        )
        return self.engine.run_check(name, timeout_sec=timeout_sec)

    def _get_session_metrics(self, args: Dict[str, Any]) -> Any:
        kw: Dict[str, Any] = {}
        v = args.get("since")
        if isinstance(v, str) and v.strip():
            kw["since"] = v.strip()
        v = args.get("group_by")
        if isinstance(v, str) and v.strip():
            kw["group_by"] = v.strip()
        if args.get("quality") is True:
            kw["quality"] = True
        return self.engine.get_session_metrics(**kw)

    def _inspect_class(self, args: Dict[str, Any]) -> Any:
        return self.engine.inspect_class(str(args.get("name", "")).strip())

    def _list_locale_keys(self, args: Dict[str, Any]) -> Any:
        ns = args.get("namespace")
        ns_str = str(ns).strip() if isinstance(ns, str) and ns.strip() else None
        return self.engine.list_locale_keys(namespace=ns_str)

    def _find_locale_key(self, args: Dict[str, Any]) -> Any:
        return self.engine.find_locale_key(str(args.get("pattern", "")))

    def _find_pattern_in_configs(self, args: Dict[str, Any]) -> Any:
        pattern = str(args.get("pattern", ""))
        kinds_raw = args.get("kinds")
        kinds: Optional[list] = None
        if isinstance(kinds_raw, list):
            kinds = [str(k) for k in kinds_raw if isinstance(k, str) and k.strip()]
        elif isinstance(kinds_raw, str) and kinds_raw.strip():
            # Accept comma-separated string too so the tool is friendly
            # to clients that don't easily pass arrays.
            kinds = [k.strip() for k in kinds_raw.split(",") if k.strip()]
        limit = 200
        if "limit" in args:
            try:
                limit = max(1, min(2000, int(args["limit"])))
            except (TypeError, ValueError):
                pass
        return self.engine.find_pattern_in_configs(
            pattern,
            kinds=kinds,
            case_sensitive=bool(args.get("case_sensitive", False)),
            use_regex=bool(args.get("use_regex", False)),
            limit=limit,
        )

    def _list_config_kinds(self, args: Dict[str, Any]) -> Any:
        return self.engine.list_config_kinds()

    def _rebuild_index(self, args: Dict[str, Any]) -> Any:
        """Re-run ``agent_map.py`` against the active project root and
        flush in-process caches so subsequent queries see the new
        artifacts. Used after the agent edits source files — replaces
        the manual ``python3 .ai-context/agent_map.py`` round-trip.

        Returns ``{"ok": bool, "duration_ms": int, "stderr_tail": str}``.
        Doesn't reload the running server itself — just rebuilds the
        JSON artefacts and resets the engine's lazy-load caches.
        """
        import subprocess
        import sys
        import time

        here = os.path.dirname(os.path.abspath(__file__))
        builder = os.path.join(os.path.dirname(here), "agent_map.py")
        if not os.path.isfile(builder):
            return {"ok": False, "error": f"agent_map.py not found at {builder}"}

        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, builder, "--root", self.engine.project_root],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout after 120s"}
        elapsed_ms = int((time.time() - t0) * 1000)

        # Reset in-process caches so the next query reads fresh JSON.
        self.engine.invalidate_caches()

        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "duration_ms": elapsed_ms,
            "stderr_tail": (proc.stderr or "")[-400:],
            "stdout_tail": (proc.stdout or "")[-400:],
        }

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

    def _ruff_violations(self, args: Dict[str, Any]) -> Any:
        kw: Dict[str, Any] = {}
        for name in ("code", "path_prefix"):
            v = args.get(name)
            if isinstance(v, str) and v.strip():
                kw[name] = v.strip()
        if "summary" in args:
            kw["summary"] = bool(args["summary"])
        if "limit" in args:
            try:
                kw["limit"] = max(0, int(args["limit"]))
            except (TypeError, ValueError):
                pass
        return self.engine.ruff_violations(**kw)

    def _ruff_format(self, args: Dict[str, Any]) -> Any:
        kw: Dict[str, Any] = {}
        v = args.get("path_prefix")
        if isinstance(v, str) and v.strip():
            kw["path_prefix"] = v.strip()
        if "summary" in args:
            kw["summary"] = bool(args["summary"])
        if "limit" in args:
            try:
                kw["limit"] = max(0, int(args["limit"]))
            except (TypeError, ValueError):
                pass
        return self.engine.ruff_format(**kw)

    def _mypy_violations(self, args: Dict[str, Any]) -> Any:
        kw: Dict[str, Any] = {}
        for name in ("code", "path_prefix", "severity"):
            v = args.get(name)
            if isinstance(v, str) and v.strip():
                kw[name] = v.strip()
        if "summary" in args:
            kw["summary"] = bool(args["summary"])
        if "limit" in args:
            try:
                kw["limit"] = max(0, int(args["limit"]))
            except (TypeError, ValueError):
                pass
        return self.engine.mypy_violations(**kw)

    def _find_in_templates(self, args: Dict[str, Any]) -> Any:
        pattern = str(args.get("pattern", "")).strip()
        match_path = args.get("match_path")
        kw: Dict[str, Any] = {}
        if isinstance(match_path, str) and match_path.strip():
            kw["match_path"] = match_path.strip()
        return self.engine.find_in_templates(pattern, **kw)

    def _ng_audit_component(self, args: Dict[str, Any]) -> Any:
        name = str(args.get("name", "")).strip()
        return self.engine.ng_audit_component(name) if name else None

    def _ng_uses_selector(self, args: Dict[str, Any]) -> Any:
        selector = str(args.get("selector", "")).strip()
        match_path = args.get("match_path")
        kw: Dict[str, Any] = {}
        if isinstance(match_path, str) and match_path.strip():
            kw["match_path"] = match_path.strip()
        return self.engine.ng_uses_selector(selector, **kw)

    def _ng_overview(self, args: Dict[str, Any]) -> Any:
        return self.engine.ng_overview()

    def _ng_inject_graph(self, args: Dict[str, Any]) -> Any:
        service = str(args.get("service", "")).strip()
        return self.engine.ng_inject_graph(service) if service else []

    def _ng_list_routes(self, args: Dict[str, Any]) -> Any:
        return self.engine.ng_list_routes()

    def _ng_route_for_path(self, args: Dict[str, Any]) -> Any:
        return self.engine.ng_route_for_path(str(args.get("path", "")))

    def _ng_routes_for_component(self, args: Dict[str, Any]) -> Any:
        name = str(args.get("name", "")).strip()
        return self.engine.ng_routes_for_component(name) if name else []
