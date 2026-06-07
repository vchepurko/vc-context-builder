"""Routes mixin — HTTP routes + Angular routes + aiogram callbacks + FSM.

Each method is a thin pass-through to the matching index module
(``route_bridge`` / ``ng_route_bridge`` / ``callback_index`` /
``fsm_flow``). Kept separate so ``query_engine.py`` doesn't have to
host two related but distinct domains (Python web routes and JS/TS
Angular routes).

Mixin contract: assumes the host class provides the lazy loaders
``_load_routes`` / ``_load_ng_routes`` / ``_load_callbacks`` /
``_load_fsm_flows``. No state of its own.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional, Tuple


class _RoutesMixin:
    """HTTP + Angular + aiogram callback / FSM lookups.

    Pure pass-throughs. The detailed contracts live on each method's
    docstring.
    """

    # Type stubs so mypy knows what the host class provides.
    project_root: str
    _llm_antipattern_cache: ClassVar[Dict[Tuple[Any, ...], List[Dict[str, Any]]]]

    def _load_routes(self) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError  # pragma: no cover

    def _load_ng_routes(self) -> List[Dict[str, Any]]:
        raise NotImplementedError  # pragma: no cover

    def _load_callbacks(self) -> Dict[str, List[Dict[str, Any]]]:
        raise NotImplementedError  # pragma: no cover

    def _load_fsm_flows(self) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError  # pragma: no cover

    # ------------------------------------------------------------------
    # Feature C — cross-language route bridge
    # ------------------------------------------------------------------

    def find_route(self, path: str) -> Optional[Dict[str, Any]]:
        """Return the full route record (with ``callers_js``) or ``None``.

        Accepts either the bare URL path (``/api/foo``) or a
        method-prefixed key (``GET /api/foo``).
        """
        from indexers.route_bridge import find_route_for_path

        return find_route_for_path(self._load_routes(), path)

    def route_callers(self, path: str) -> List[Dict[str, Any]]:
        """JS/TS call-sites for the given route path. Empty when none/missing."""
        from indexers.route_bridge import callers_for_route

        return callers_for_route(self._load_routes(), path)

    def route_for_js_call(self, file_path: str) -> List[Dict[str, Any]]:
        """Routes whose ``callers_js`` list mentions ``file_path``."""
        from indexers.route_bridge import route_for_js_file

        return route_for_js_file(self._load_routes(), file_path)

    # ------------------------------------------------------------------
    # Feature R — Angular RouterModule path → component map.
    # ------------------------------------------------------------------

    def ng_list_routes(self) -> List[Dict[str, Any]]:
        """All extracted Angular routes, in (file, line) order. Empty
        list on non-Angular projects (no agent_ng_routes.json)."""
        return list(self._load_ng_routes())

    def ng_route_for_path(self, path: str) -> List[Dict[str, Any]]:
        """Resolve an Angular URL path to route records.

        Exact match wins; falls back to substring contains so a query
        for ``users`` finds ``users/:id`` and ``admin/users``. Strips a
        leading slash before matching so callers can use either form.
        """
        from indexers.ng_route_bridge import route_for_path as _rp

        if path is None:
            return []
        normalised = path.lstrip("/")
        return _rp(self._load_ng_routes(), normalised)

    def ng_routes_for_component(self, name: str) -> List[Dict[str, Any]]:
        """Reverse lookup — every route whose ``component`` is *name*."""
        from indexers.ng_route_bridge import routes_for_component as _rfc

        return _rfc(self._load_ng_routes(), name)

    # ------------------------------------------------------------------
    # Feature D — aiogram callback_data resolver
    # ------------------------------------------------------------------

    def find_callback(
        self,
        data: str,
        *,
        include_tests: bool = False,
    ) -> List[Dict[str, Any]]:
        """Resolve an aiogram ``callback_data`` string to its handler(s).

        Tries an exact lookup first, then falls back to the longest
        matching ``startswith`` prefix. Empty list when nothing matches
        or the index is missing.

        ``include_tests`` defaults to False — production callback
        handlers are what callers usually want. Test fixtures that
        bind handlers to throwaway data strings are filtered out.
        """
        from indexers.callback_index import find_callback as _find
        from test_analysis._test_filter import filter_test_records

        hits = _find(self._load_callbacks(), data)
        return filter_test_records(hits, include_tests=include_tests)

    def find_anti_patterns(
        self,
        rule: str,
    ) -> List[Dict[str, Any]]:
        """Run one registered anti-pattern detector. Returns
        ``[{rule, file, line, function?, evidence}]``. Empty list for
        unknown rule names (cross-check with :meth:`list_anti_patterns`)
        or when the project is clean.

        Static rules (fast, AST-only):
        * ``aiogram-state-check-in-body`` — ``@router.message(F.<...>)``
          without a state filter. CLAUDE.md silent-dispatch killer.

        LLM-based custom rules defined in ``.vc-context/conventions.json``
        ``anti_patterns`` array are also checked; requires ``chat_provider``
        to be configured.
        """
        from anti_patterns import (  # type: ignore[import-not-found]
            detect_with_llm,
            has_static_rule,
            load_llm_rules,
        )
        from anti_patterns import (
            find_anti_patterns as _find,
        )

        if has_static_rule(rule):
            return _find(self.project_root, rule)

        llm_rules = load_llm_rules(self.project_root)
        rule_def = next((r for r in llm_rules if r["name"] == rule), None)
        if rule_def is None:
            return []

        try:
            from ollama_chat import chat_provider_from_conventions  # type: ignore[import-not-found]

            chat = chat_provider_from_conventions(self.project_root)
            if chat is None:
                return []
            return detect_with_llm(self.project_root, rule_def, chat, self._llm_antipattern_cache)
        except Exception:
            return []

    def list_anti_patterns(self) -> List[str]:
        """All registered anti-pattern rule names (static + custom LLM
        rules from ``.vc-context/conventions.json``), sorted.
        """
        from anti_patterns import list_anti_patterns as _list  # type: ignore[import-not-found]
        from anti_patterns import load_llm_rules

        static = _list()
        llm = [r["name"] for r in load_llm_rules(self.project_root)]
        return sorted(set(static) | set(llm))

    def find_orphan_callbacks(
        self,
        *,
        include_tests: bool = False,
    ) -> List[Dict[str, Any]]:
        """Anti-pattern detector — every literal ``callback_data="..."``
        reference with no matching ``@router.callback_query`` handler.

        Set-difference between AST-walked ``callback_data="..."``
        button references and the handler index. Non-literal call
        sites (f-strings, ``.format()``, variables) are silently
        skipped. ``include_tests`` defaults to False — orphan refs
        in ``tests/`` are usually intentional fixtures.

        Each record: ``{data, file, line}``, sorted by
        ``(file, line)``.
        """
        from indexers.callback_index import find_orphans as _orphans

        return _orphans(
            self.project_root,
            self._load_callbacks(),
            include_tests=include_tests,
        )

    # ------------------------------------------------------------------
    # Feature F — aiogram FSM flow graph
    # ------------------------------------------------------------------

    def trace_fsm_flow(self, state: str) -> Optional[Dict[str, Any]]:
        """Resolve an FSM state to its lifecycle graph.

        Accepts the full ``StatesGroup.field`` form or a bare field name
        when it's unambiguous. Returns ``None`` for unknown / ambiguous
        states or when the index is missing.
        """
        from fsm_flow import trace_fsm_flow as _trace  # type: ignore[import-not-found]

        return _trace(self._load_fsm_flows(), state)
