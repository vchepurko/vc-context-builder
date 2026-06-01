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

    def lint_violations(self) -> List[Dict[str, Any]]:
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
        from indexers.locale_index import list_keys as _list

        return _list(self._load_locale_keys(), namespace=namespace)

    def find_locale_key(self, pattern: str) -> List[str]:
        """Substring (case-insensitive) match across keys. For "every
        key starting with ``staff_``" pass ``"staff_"``."""
        from indexers.locale_index import find_keys as _find

        return _find(self._load_locale_keys(), pattern)

    def get_locale_key(self, key: str) -> Optional[Dict[str, Any]]:
        """Full entry for a key — namespace, languages it lives in,
        per-language values, and the ``missing`` list (languages whose
        namespace file exists but doesn't carry this key — handy for
        parity audits)."""
        from indexers.locale_index import get_key as _get

        return _get(self._load_locale_keys(), key)

    def find_local_agents_md(self, path: str) -> List[Dict[str, Any]]:
        """Walk up from ``path`` (file or directory) and return every
        ``AGENTS.md`` along the way, most-specific first.

        Use to discover folder-scoped invariants without a filesystem
        walk — e.g. before editing ``bot/handlers/admin.py`` ask
        ``find_local_agents_md("bot/handlers/admin.py")`` to see the
        per-folder ``AGENTS.md`` plus any closer / deeper rules.

        ``path`` is resolved against ``project_root`` and rejected if
        it escapes the tree. Walks stop at the project root — the
        top-level ``AGENTS.md`` (if any) is the most-general entry.

        Each record: ``{file, size_bytes}``, ordered closest-first.
        Empty list when nothing is found (project doesn't use the
        convention) or the path is outside the project.
        """
        import os as _os  # local — avoid mixin-module name collision

        if not path:
            return []
        project_root = _os.path.abspath(self.project_root)
        abs_path = _os.path.abspath(_os.path.join(project_root, path))
        try:
            if _os.path.commonpath([project_root, abs_path]) != project_root:
                return []
        except ValueError:
            return []
        cur = abs_path if _os.path.isdir(abs_path) else _os.path.dirname(abs_path)
        if not cur:
            cur = project_root
        out: List[Dict[str, Any]] = []
        while True:
            candidate = _os.path.join(cur, "AGENTS.md")
            if _os.path.isfile(candidate):
                try:
                    size = _os.path.getsize(candidate)
                except OSError:
                    size = 0
                rel = _os.path.relpath(candidate, project_root).replace(_os.sep, "/")
                out.append({"file": rel, "size_bytes": size})
            if cur == project_root:
                break
            parent = _os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return out

    def record_bash_usage(
        self,
        count: int = 1,
        action: Optional[str] = None,
        bytes_estimate: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Self-reported Bash usage marker — light-touch telemetry.

        Agents call this after a shell-out so the dispatcher's
        automatic metrics record (``record_bash_usage`` tool) shows
        up alongside MCP calls in ``get_session_metrics``. The "true
        MCP win" ratio is then accurate: today metrics see only the
        MCP side, so Bash-grep / sed / node usage is invisible.

        The method itself is a near no-op — it just echoes the input
        so the auto-record captures ``count`` / ``action`` /
        ``bytes_estimate`` in the JSONL line. The aggregation lives
        in ``get_session_metrics`` (group_by=tool surfaces it as
        ``by_tool['record_bash_usage']``).

        Parameters
        ----------
        count: occurrence count (default 1) — agents can batch
            multiple shell-outs into one call.
        action: optional free-text hint (``"grep"``, ``"sed-bulk"``,
            ``"node --check"``).
        bytes_estimate: optional rough size of the Bash output for
            baseline-savings calculations. Capped at 0 if negative.

        Returns ``{ok, count, action?, bytes_estimate?}``.
        """
        out: Dict[str, Any] = {"ok": True, "count": max(1, int(count or 1))}
        if action:
            out["action"] = str(action)
        if bytes_estimate is not None:
            try:
                out["bytes_estimate"] = max(0, int(bytes_estimate))
            except (TypeError, ValueError):
                pass
        return out

    def find_locale_drift(
        self,
        namespace: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Anti-pattern detector — every locale key present in one
        language but missing in a sibling that owns the same namespace
        file. Returns ``[{key, namespace, present, missing}, ...]``
        sorted by ``(namespace, key)``.

        Optional ``namespace`` scopes the audit to one namespace
        (``"common"`` / ``"admin"`` / etc.). Empty list when every key
        is fully translated — that's the parity-OK signal.

        Drives translation review without a dedicated diff tool.
        """
        from indexers.locale_index import find_drift as _drift

        return _drift(self._load_locale_keys(), namespace=namespace)

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
    ) -> List[Dict[str, Any]]:
        """Search non-code config files (env / yaml / Caddyfile / …) for
        a substring or regex. Replaces ``grep -rn`` for "where is
        ``GOOGLE_OAUTH_*`` referenced" style questions. Stdlib-only
        on-demand scan — no persistent index.

        Returns a list of ``{file, line, kind, text}`` dicts, bounded by
        ``limit`` (default 200).
        """
        from indexers.configs_scanner import scan

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
        from indexers.configs_scanner import list_kinds as _kinds

        return _kinds()

    # ------------------------------------------------------------------
    # DevOps snapshot (gap-closer)
    # ------------------------------------------------------------------

    def devops_card(self) -> Dict[str, Any]:
        """One-call snapshot of the project's deployment surface —
        docker-compose services, Dockerfiles, Caddy sites, GitHub
        Actions workflows, scheduler-job role members. Replaces the
        scattered "cat compose / grep Caddyfile / ls workflows /
        find_by_role" sequence agents fall into when investigating
        "where does deploy live?".
        """
        from devops_card import build as _build  # type: ignore[import-not-found]

        scheduler_jobs: List[str] = []
        try:
            scheduler_jobs = list(self.find_by_role("scheduler-job"))  # type: ignore[attr-defined]
        except Exception:
            scheduler_jobs = []
        return _build(self.project_root, scheduler_jobs=scheduler_jobs)

    # ------------------------------------------------------------------
    # ORM field usage (gap-closer)
    # ------------------------------------------------------------------

    def find_orm_field_usage(
        self,
        model: str,
        column: str,
        *,
        limit: int = 200,
        include_tests: bool = False,
    ) -> List[Dict[str, Any]]:
        """Every read/write of ``<Model>.<column>`` (or
        ``<model>.<column>`` when the variable name follows the class
        name). Replaces ``grep -rn photo_file_id`` for refactor scoping
        — returns precise AST-anchored matches with read/write kind.

        ``include_tests`` defaults to False — for refactor-impact
        analysis test fixtures usually overstate "where is this field
        touched?". Pass True when explicitly auditing test coverage of
        a field.
        """
        from indexers.orm_field_usage import find_usage
        from test_analysis._test_filter import filter_test_records

        hits = find_usage(self.project_root, model, column, limit=limit)
        return filter_test_records(hits, include_tests=include_tests)

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
        from linters.ruff_inspector import collect as _collect

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
        from linters.ruff_format_inspector import collect as _collect

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
        from linters.mypy_inspector import collect as _collect

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
        baseline: bool = False,
    ) -> Dict[str, Any]:
        """Aggregate this project's per-call metrics.

        ``since`` accepts ``"24h"``, ``"7d"``, ``"today"``, ``"all"``.
        Default = ``"today"``.  ``group_by`` is one of ``"tool"``
        (default), ``"hour"``, or ``"empty"``.

        When ``quality=True``, also compute Phase-2 quality signals
        (wasteful round-trips, hot rereads, empty streaks) from the
        same JSONL stream and embed them under the ``quality`` key.
        Detectors are conservative — see ``mcp.quality`` for thresholds.

        When ``baseline=True``, embed a heuristic estimate of the
        Bash-fallback token cost for the same query mix — handy for
        answering "how much is MCP actually saving me?" without paying
        the cost of full dual-execution shadow mode. Tools without a
        sensible Bash equivalent (``read_slice``, ``run_check``,
        ``rebuild_index``, telemetry itself) contribute 0 to the
        baseline; empty results also contribute 0. See
        :data:`mcp.metrics._BASELINE_BYTES_PER_TOOL` for per-tool
        numbers.

        Returns the shape produced by :func:`mcp.metrics.aggregate`:
        ``{calls, total_tokens, avg_t_ms, empty_ratio, ok_ratio,
        by_<group>, quality?, baseline?}``.  Empty payload when the
        writer has never run for this project.
        """
        from mcp.metrics import aggregate, read_metrics

        entries = read_metrics(
            self.project_root,
            since=since if since is not None else "today",
        )
        out = aggregate(entries, group_by=group_by, baseline=baseline)
        if quality:
            from mcp.quality import quality_report

            out["quality"] = quality_report(entries)
        return out

    # ------------------------------------------------------------------
    # Composite health roll-up — one call covers four inspectors
    # ------------------------------------------------------------------

    def find_in_file(
        self,
        file: str,
        pattern: str,
        *,
        use_regex: bool = False,
        case_sensitive: bool = False,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Surgical grep over a single file — returns
        ``[{line, text}]`` matches capped at ``limit``.

        Closes the "I know the file, I'm hunting a string inside it"
        case that ``find_symbol`` can't help with (top-level shape
        only) and that today drops to Bash ``grep -n``. Path is
        resolved against ``project_root`` and rejected if it escapes
        the tree. Files larger than 5 MB are skipped to bound work.
        """
        from file_grep import grep_file  # type: ignore[import-not-found]

        return grep_file(
            self.project_root,
            file,
            pattern,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            limit=limit,
        )

    def check_health(self, *, summary: bool = True) -> Dict[str, Any]:
        """One-call code-health roll-up: lint + mypy + ruff + ruff-format
        in a single MCP round-trip.

        Replaces the typical 4-call sequence (`lint_violations` →
        `mypy_violations` → `ruff_violations` → `ruff_format`) with one
        bounded response. Real-session telemetry (klodchickknifes,
        May 2026) showed 33 calls split 11/11/11 between the three
        inspectors, almost always returning empty — exactly the
        pattern this tool collapses.

        Returns
        -------
        ``{lint, mypy, ruff, format}`` where:

        - ``lint`` — raw convention-lint violations list (the
          convention linter has no summary mode).
        - ``mypy`` / ``ruff`` / ``format`` — structured breakdowns
          from their dedicated inspectors. ``summary=True`` (default)
          drops the per-violation list and returns counts only,
          keeping the response under ~250 bytes when the codebase is
          clean. Pass ``summary=False`` to embed the full lists.
        """
        return {
            "lint": self.lint_violations(),
            "mypy": self.mypy_violations(summary=summary),
            "ruff": self.ruff_violations(summary=summary),
            "format": self.ruff_format(summary=summary),
        }
