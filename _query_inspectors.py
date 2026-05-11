"""Inspector mixin — keep `query_engine.py` from sprawling.

Moved out of `QueryEngine`'s body because they share one shape:
each method is a thin pass-through to a sibling inspector module
(`locale_index`, `notify_log_reader`, `ruff_inspector`,
`ruff_format_inspector`, `mypy_inspector`, `mcp.metrics`/`mcp.quality`).

Public surface stays unchanged — `QueryEngine(_InspectorsMixin)`
exposes the methods exactly as before.

Mixin contract: assumes the host class provides
``self.project_root: str`` and the lazy loader
``self._load_locale_keys()``. No state of its own.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class _InspectorsMixin:
    """Locales / notify / ruff / mypy / format / telemetry.

    Pure pass-throughs. The detailed contracts live on each method's
    docstring; the original file kept them grouped here for the same
    reason.
    """

    # Type stubs so mypy knows what the host class provides.
    # Concrete values come from QueryEngine.__init__.
    project_root: str

    def _load_locale_keys(self) -> Dict[str, Dict[str, Any]]:
        """Provided by the host class; declared here so mypy is quiet."""
        raise NotImplementedError  # pragma: no cover — overridden by host

    # ------------------------------------------------------------------
    # Locale keys (Feature I)
    # ------------------------------------------------------------------

    def list_locale_keys(self, namespace: Optional[str] = None) -> List[str]:
        """All translation keys (sorted), optionally filtered to one
        namespace. Empty list when the locale index is missing —
        graceful degradation for projects without a ``locales/`` tree.
        """
        from locale_index import list_keys as _list  # type: ignore[import-not-found]

        return _list(self._load_locale_keys(), namespace=namespace)

    def find_locale_key(self, pattern: str) -> List[str]:
        """Substring (case-insensitive) match across keys. For "every
        key starting with ``staff_``" pass ``"staff_"``."""
        from locale_index import find_keys as _find  # type: ignore[import-not-found]

        return _find(self._load_locale_keys(), pattern)

    def get_locale_key(self, key: str) -> Optional[Dict[str, Any]]:
        """Full entry for a key — namespace, languages it lives in,
        per-language values, and the ``missing`` list (languages whose
        namespace file exists but doesn't carry this key — handy for
        parity audits)."""
        from locale_index import get_key as _get  # type: ignore[import-not-found]

        return _get(self._load_locale_keys(), key)

    # ------------------------------------------------------------------
    # Config-file pattern search (Feature C — gap-closer)
    # ------------------------------------------------------------------

    def find_pattern_in_configs(
        self,
        pattern: str,
        *,
        kinds: Optional[List[str]] = None,
        case_sensitive: bool = False,
        use_regex: bool = False,
        limit: int = 200,
    ) -> List[Dict[str, object]]:
        """Search non-code config files (env / yaml / Caddyfile / …) for
        a substring or regex. Replaces ``grep -rn`` for "where is
        ``GOOGLE_OAUTH_*`` referenced" style questions. Stdlib-only
        on-demand scan — no persistent index.

        Returns a list of ``{file, line, kind, text}`` dicts, bounded by
        ``limit`` (default 200).
        """
        from configs_scanner import scan  # type: ignore[import-not-found]

        return scan(
            self.project_root,
            pattern,
            kinds=kinds,
            case_sensitive=case_sensitive,
            use_regex=use_regex,
            limit=limit,
        )

    def list_config_kinds(self) -> List[str]:
        """All known config kinds usable with ``find_pattern_in_configs``."""
        from configs_scanner import list_kinds as _kinds  # type: ignore[import-not-found]

        return _kinds()

    # ------------------------------------------------------------------
    # Notification audit log (Feature N)
    # ------------------------------------------------------------------

    def notify_log_search(
        self,
        *,
        kind: Optional[str] = None,
        recipient: Optional[int] = None,
        channel: Optional[str] = None,
        outcome: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Filtered list of audit records from the rotating JSONL log
        emitted by the parent project's ``services/notify/log.py``.

        Filters AND-combine. Empty filters return up to ``limit``
        most-recent records. Projects without a ``logs/notify.jsonl``
        return ``[]``.
        """
        from notify_log_reader import search as _search  # type: ignore[import-not-found]

        return _search(
            self.project_root,
            kind=kind,
            recipient=recipient,
            channel=channel,
            outcome=outcome,
            since=since,
            limit=limit,
        )

    def notify_log_stats(
        self,
        *,
        since: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aggregate counters: total deliveries, plus
        ``by_kind`` / ``by_channel`` outcome breakdowns. Optional
        ``since`` cuts off records older than the relative window."""
        from notify_log_reader import stats as _stats  # type: ignore[import-not-found]

        return _stats(self.project_root, since=since)

    # ------------------------------------------------------------------
    # Ruff violations / format inspectors (Feature O)
    # ------------------------------------------------------------------

    def ruff_violations(
        self,
        *,
        code: Optional[str] = None,
        path_prefix: Optional[str] = None,
        summary: bool = False,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Run ``ruff check`` in JSON mode and return the structured
        breakdown ``{total, by_code, by_file, violations?}``.

        Use ``summary=True`` first to triage the shape of failures
        (which rule codes dominate, which files concentrate them),
        then drill in with ``code=...`` / ``path_prefix=...`` for the
        per-violation list. ``limit`` caps the violations list to
        keep MCP responses bounded.
        """
        from ruff_inspector import collect as _collect  # type: ignore[import-not-found]

        return _collect(
            self.project_root,
            code=code,
            path_prefix=path_prefix,
            summary=summary,
            limit=limit,
        )

    def ruff_format(
        self,
        *,
        path_prefix: Optional[str] = None,
        summary: bool = False,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Run ``ruff format --check`` and return the list of files
        that would be reformatted: ``{total, files?}``.

        ``summary=True`` returns just ``{total: N}`` — minimum-token
        signal for "is the codebase formatted?". Use the default to
        get the file paths so a follow-up ``ruff format`` knows what
        will move.
        """
        from ruff_format_inspector import collect as _collect  # type: ignore[import-not-found]

        return _collect(
            self.project_root,
            path_prefix=path_prefix,
            summary=summary,
            limit=limit,
        )

    def mypy_violations(
        self,
        *,
        code: Optional[str] = None,
        path_prefix: Optional[str] = None,
        severity: Optional[str] = None,
        summary: bool = False,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Run ``mypy --output=json`` and return the structured
        breakdown ``{total, by_code, by_file, violations?}``.

        Use ``summary=True`` first to triage which error codes /
        files dominate, then drill in with ``code=...`` /
        ``path_prefix=...`` / ``severity=...`` for the per-violation
        list. ``limit`` caps the violations list to keep MCP
        responses bounded. Auto-skips on non-Python projects or
        projects without mypy config.
        """
        from mypy_inspector import collect as _collect  # type: ignore[import-not-found]

        return _collect(
            self.project_root,
            code=code,
            path_prefix=path_prefix,
            severity=severity,
            summary=summary,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Telemetry — per-call metrics aggregated for the user
    # ------------------------------------------------------------------

    def get_session_metrics(
        self,
        *,
        since: Optional[str] = None,
        group_by: str = "tool",
        quality: bool = False,
    ) -> Dict[str, Any]:
        """Aggregate this project's per-call metrics.

        ``since`` accepts ``"24h"``, ``"7d"``, ``"today"``, ``"all"``.
        Default = ``"today"``.  ``group_by`` is one of ``"tool"``
        (default), ``"hour"``, or ``"empty"``.

        When ``quality=True``, also compute Phase-2 quality signals
        (wasteful round-trips, hot rereads, empty streaks) from the
        same JSONL stream and embed them under the ``quality`` key.
        Detectors are conservative — see ``mcp.quality`` for thresholds.

        Returns the shape produced by :func:`mcp.metrics.aggregate`:
        ``{calls, total_tokens, avg_t_ms, empty_ratio, ok_ratio,
        by_<group>, quality?}``.  Empty payload when the writer has
        never run for this project.
        """
        from mcp.metrics import aggregate, read_metrics

        entries = read_metrics(
            self.project_root,
            since=since if since is not None else "today",
        )
        out = aggregate(entries, group_by=group_by)
        if quality:
            from mcp.quality import quality_report

            out["quality"] = quality_report(entries)
        return out
