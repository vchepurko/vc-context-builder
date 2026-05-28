"""Tests mixin — test linkage, coverage, and unit/integration split.

Each method projects over the prebuilt ``agent_tests.json`` and
``agent_test_categories.json`` artefacts. ``find_test`` falls back
to a live scan via ``test_linking`` so the tool stays useful before
the builder has run on a fresh checkout.

Mixin contract: assumes the host class provides
``self.project_root``, the lazy loaders ``_load_symbols`` /
``_load_tests`` / ``_load_test_categories``, the ``_symbols_get``
helper, and ``find_by_role`` (used by ``coverage_for_role`` to
expand role umbrellas).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _pct(numerator: int, denominator: int) -> float:
    """Coverage percentage rounded to one decimal — 0.0 when denom=0."""
    if denominator <= 0:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


class _TestsMixin:
    """Test linkage + coverage + classification.

    Pure projections + a live-scan fallback for find_test. Detailed
    contracts on each method.
    """

    # Type stubs so mypy knows what the host class provides.
    project_root: str

    def _load_symbols(self) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError  # pragma: no cover

    def _load_tests(self) -> Dict[str, Any]:
        raise NotImplementedError  # pragma: no cover

    def _load_test_categories(self) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError  # pragma: no cover

    def _symbols_get(self, name: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError  # pragma: no cover

    def find_by_role(self, role: str) -> List[str]:
        raise NotImplementedError  # pragma: no cover

    # ------------------------------------------------------------------
    # Feature B — test linking
    # ------------------------------------------------------------------

    def find_test(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return the test record for ``symbol`` or ``None``.

        Reads from the prebuilt ``agent_tests.json`` when available;
        falls back to a live scan via ``test_linking.find_test_for_symbol``
        so the tool still works before the builder has run.
        """
        tests = self._load_tests()
        if symbol in tests:
            entry = tests[symbol]
            return dict(entry) if isinstance(entry, dict) else None

        # Live fallback — useful right after a fresh symbol lands and
        # the user hasn't rebuilt yet.
        symbol_entry = self._symbols_get(symbol)
        if symbol_entry is None:
            return None
        from test_analysis.test_linking import find_test_for_symbol

        return find_test_for_symbol(self.project_root, symbol, symbol_entry.get("file") or "")

    def coverage_stats(self) -> Dict[str, Dict[str, int]]:
        """Return per-role coverage counts plus an overall total.

        Shape: ``{role_or_'overall': {with_test, total}}``.
        Roles without an entry in ``agent_root.json`` are silently
        omitted; symbols without a role are counted only in
        ``overall``.
        """
        symbols = self._load_symbols()
        tests = self._load_tests()

        def _has_test(name: str) -> bool:
            entry = tests.get(name)
            return isinstance(entry, dict) and bool(entry.get("test_file"))

        role_buckets: Dict[str, Dict[str, int]] = {}
        for name, entry in symbols.items():
            role = entry.get("role") if isinstance(entry, dict) else None
            if role:
                bucket = role_buckets.setdefault(role, {"with_test": 0, "total": 0})
                bucket["total"] += 1
                if _has_test(name):
                    bucket["with_test"] += 1

        overall = {"with_test": 0, "total": 0}
        for name in symbols:
            overall["total"] += 1
            if _has_test(name):
                overall["with_test"] += 1
        # Keep the overall bucket last for stable rendering.
        ordered: Dict[str, Dict[str, int]] = {r: role_buckets[r] for r in sorted(role_buckets)}
        ordered["overall"] = overall
        return ordered

    # ------------------------------------------------------------------
    # Feature G — coverage by role (one-tool surface for QA gaps)
    # ------------------------------------------------------------------

    def coverage_for_role(self, role: Optional[str] = None) -> Dict[str, Any]:
        """Test-coverage view, scoped or whole-project.

        See ``QueryEngine.coverage_for_role`` docstring history for
        the full shape contract — kept verbatim here so the mixin
        is the single source of truth.
        """
        symbols = self._load_symbols()
        tests = self._load_tests()

        def _test_entry(name: str) -> Optional[Dict[str, Any]]:
            entry = tests.get(name)
            if isinstance(entry, dict) and entry.get("test_file"):
                return entry
            return None

        if role is None:
            stats = self.coverage_stats()
            roles_out: Dict[str, Dict[str, Any]] = {}
            overall_bucket: Dict[str, Any] = {}
            for k, v in stats.items():
                payload = {
                    "total": v["total"],
                    "with_test": v["with_test"],
                    "coverage_pct": _pct(v["with_test"], v["total"]),
                }
                if k == "overall":
                    overall_bucket = payload
                else:
                    roles_out[k] = payload
            return {"roles": roles_out, "overall": overall_bucket}

        # Build the symbol pool — supports legacy umbrellas (e.g.
        # ``aiogram-handler``) by reusing find_by_role's expansion.
        pool = set(self.find_by_role(role))

        missing: List[Dict[str, Any]] = []
        covered: List[Dict[str, Any]] = []
        for name in pool:
            entry = symbols.get(name)
            file = entry.get("file") if isinstance(entry, dict) else None
            test = _test_entry(name)
            if test is None:
                missing.append({"name": name, "file": file})
            else:
                covered.append(
                    {
                        "name": name,
                        "file": file,
                        "test_file": test.get("test_file"),
                        "test_function": test.get("test_function"),
                    }
                )
        # Stable ordering — alpha by name.
        missing.sort(key=lambda r: r["name"])
        covered.sort(key=lambda r: r["name"])
        total = len(pool)
        with_test = len(covered)
        return {
            "role": role,
            "total": total,
            "with_test": with_test,
            "coverage_pct": _pct(with_test, total),
            "missing": missing,
            "covered": covered,
        }

    def find_handlers_without_tests(
        self,
        role: str = "aiogram-handler",
    ) -> List[Dict[str, Any]]:
        """Anti-pattern detector — every symbol with the given handler
        ``role`` that has no linked test entry.

        Sugar over ``coverage_for_role(role)["missing"]``, enriched with
        ``line`` / ``kind`` from the symbol index so the agent can jump
        straight to the source without a follow-up ``find_symbol``.

        ``role`` defaults to ``"aiogram-handler"`` (the legacy umbrella
        — expands to ``callback-handler`` / ``command-handler`` /
        ``fsm-message-handler`` / ``text-match-handler`` /
        ``catch-all-handler``). Pass another role for project-specific
        handler taxonomies (e.g. ``"webhook"``).

        Each record: ``{name, role, file, line, kind}``, sorted by
        ``(file, line)``. Empty list when every handler has coverage —
        the parity-OK signal.
        """
        coverage = self.coverage_for_role(role)
        missing = coverage.get("missing") or []
        symbols = self._load_symbols()
        out: List[Dict[str, Any]] = []
        for rec in missing:
            name = rec.get("name")
            if not name:
                continue
            entry = symbols.get(name) or {}
            out.append(
                {
                    "name": name,
                    "role": role,
                    "file": rec.get("file") or entry.get("file"),
                    "line": entry.get("line"),
                    "kind": entry.get("kind"),
                }
            )
        out.sort(key=lambda r: (r.get("file") or "", r.get("line") or 0))
        return out

    # ------------------------------------------------------------------
    # Feature H — test categorisation (unit / integration / unknown)
    # ------------------------------------------------------------------

    def classify_tests(self) -> Dict[str, Any]:
        """Return ``{summary, files}`` for the whole test suite.

        ``summary`` is ``{category → count}``; ``files`` is the raw
        ``{rel_path → {category, signals}}`` map. Empty containers when
        the artifact is missing.
        """
        from test_analysis.test_classifier import category_summary

        index = self._load_test_categories()
        return {
            "summary": category_summary(index),
            "files": index,
        }

    def tests_by_category(self, category: str) -> List[str]:
        """File paths for ``category`` (``"unit"`` / ``"integration"`` /
        ``"unknown"``). Sorted, deduped, empty list on miss."""
        from test_analysis.test_classifier import (
            lookup_tests_by_category as _by,
        )

        return _by(self._load_test_categories(), category)
