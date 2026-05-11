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

from typing import Any, Dict, List, Optional


class _RoutesMixin:
    """HTTP + Angular + aiogram callback / FSM lookups.

    Pure pass-throughs. The detailed contracts live on each method's
    docstring.
    """

    # Type stubs so mypy knows what the host class provides.
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
        from route_bridge import find_route_for_path  # type: ignore[import-not-found]

        return find_route_for_path(self._load_routes(), path)

    def route_callers(self, path: str) -> List[Dict[str, Any]]:
        """JS/TS call-sites for the given route path. Empty when none/missing."""
        from route_bridge import callers_for_route  # type: ignore[import-not-found]

        return callers_for_route(self._load_routes(), path)

    def route_for_js_call(self, file_path: str) -> List[Dict[str, Any]]:
        """Routes whose ``callers_js`` list mentions ``file_path``."""
        from route_bridge import route_for_js_file  # type: ignore[import-not-found]

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
        from ng_route_bridge import route_for_path as _rp  # type: ignore[import-not-found]

        if path is None:
            return []
        normalised = path.lstrip("/")
        return _rp(self._load_ng_routes(), normalised)

    def ng_routes_for_component(self, name: str) -> List[Dict[str, Any]]:
        """Reverse lookup — every route whose ``component`` is *name*."""
        from ng_route_bridge import routes_for_component as _rfc  # type: ignore[import-not-found]

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
        from _test_filter import filter_test_records  # type: ignore[import-not-found]
        from callback_index import find_callback as _find  # type: ignore[import-not-found]

        hits = _find(self._load_callbacks(), data)
        return filter_test_records(hits, include_tests=include_tests)

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
