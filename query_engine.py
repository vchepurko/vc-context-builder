"""Shared query engine for the vc-context-builder artifacts.

A single ``QueryEngine`` instance lazy-loads ``agent_root.json``,
``agent_symbols.json``, and the per-folder ``_module_map.json`` files
written by ``agent_map.py``. It exposes a small, RPC-friendly surface
so CLI users and MCP clients never have to load the raw JSON into
their context window.

Stdlib only by design — the builder is zero-dependency, and so is
this layer.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _pct(numerator: int, denominator: int) -> float:
    """Coverage percentage rounded to one decimal — 0.0 when denom=0."""
    if denominator <= 0:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


class QueryEngine:
    """Lazy-loading reader over the three artifact tiers.

    Parameters
    ----------
    project_root:
        Absolute or relative path to the directory that holds
        ``agent_root.json`` / ``agent_symbols.json``. Per-folder
        module maps are discovered by walking from this root.

    Notes
    -----
    All loaders are lazy: nothing is read until the first method call
    that needs that tier. Subsequent calls are served from in-memory
    caches, so the engine is safe to keep around for the lifetime of
    a CLI invocation or MCP server process.

    The engine is **read-only** — it never mutates the artifacts. Only
    ``agent_map.py`` writes them.
    """

    ROOT_FILENAME = "agent_root.json"
    SYMBOLS_FILENAME = "agent_symbols.json"
    TESTS_FILENAME = "agent_tests.json"
    ROUTES_FILENAME = "agent_routes.json"
    CALLBACKS_FILENAME = "agent_callbacks.json"
    FSM_FLOW_FILENAME = "agent_fsm_flows.json"
    TEST_CATEGORIES_FILENAME = "agent_test_categories.json"
    MAP_FILENAME = "_module_map.json"
    IGNORE_DIRS = {
        ".git", "node_modules", "vendor", "__pycache__",
        "dist", "build", ".venv", "venv", ".idea", ".vscode",
    }

    def __init__(self, project_root: str = ".") -> None:
        self.project_root = os.path.abspath(project_root)
        self._root: Optional[Dict[str, Any]] = None
        self._symbols: Optional[Dict[str, Dict[str, Any]]] = None
        # Each entry: (rel_dir, parsed_map_json)
        self._module_maps: Optional[List[Tuple[str, Dict[str, Any]]]] = None
        # Reverse-index for who_calls — built once on demand.
        self._reverse_deps: Optional[Dict[str, List[Dict[str, str]]]] = None
        # Optional per-tier caches for the new artifacts. ``None`` =
        # not yet attempted; ``{}`` = read but artifact absent / empty.
        self._tests: Optional[Dict[str, Any]] = None
        self._routes: Optional[Dict[str, Dict[str, Any]]] = None
        self._callbacks: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._fsm_flows: Optional[Dict[str, Dict[str, Any]]] = None
        self._test_categories: Optional[Dict[str, Dict[str, Any]]] = None

    # ------------------------------------------------------------------
    # Lazy loaders
    # ------------------------------------------------------------------

    def _load_root(self) -> Dict[str, Any]:
        if self._root is None:
            path = os.path.join(self.project_root, self.ROOT_FILENAME)
            with open(path, "r", encoding="utf-8") as fh:
                self._root = json.load(fh)
        return self._root

    def _load_symbols(self) -> Dict[str, Dict[str, Any]]:
        if self._symbols is None:
            path = os.path.join(self.project_root, self.SYMBOLS_FILENAME)
            with open(path, "r", encoding="utf-8") as fh:
                self._symbols = json.load(fh)
        return self._symbols

    def _load_tests(self) -> Dict[str, Any]:
        """Return ``agent_tests.json`` content (or ``{}`` if missing).

        Unlike the root/symbols loaders, a missing artifact is NOT an
        error — Feature B degrades gracefully when the builder didn't
        generate it.
        """
        if self._tests is None:
            path = os.path.join(self.project_root, self.TESTS_FILENAME)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self._tests = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._tests = {}
        return self._tests

    def _load_routes(self) -> Dict[str, Dict[str, Any]]:
        """Return ``agent_routes.json`` content (or ``{}`` if missing)."""
        if self._routes is None:
            path = os.path.join(self.project_root, self.ROUTES_FILENAME)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self._routes = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._routes = {}
        return self._routes

    def _load_callbacks(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return ``agent_callbacks.json`` content (or ``{}`` if missing).

        Same graceful-degradation contract as the other optional
        artifact loaders — Feature D is fresh and may be absent on
        older builds.
        """
        if self._callbacks is None:
            path = os.path.join(self.project_root, self.CALLBACKS_FILENAME)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self._callbacks = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._callbacks = {}
        return self._callbacks

    def _load_fsm_flows(self) -> Dict[str, Dict[str, Any]]:
        """Return ``agent_fsm_flows.json`` content (or ``{}`` if missing)."""
        if self._fsm_flows is None:
            path = os.path.join(self.project_root, self.FSM_FLOW_FILENAME)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self._fsm_flows = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._fsm_flows = {}
        return self._fsm_flows

    def _load_test_categories(self) -> Dict[str, Dict[str, Any]]:
        """Return ``agent_test_categories.json`` (or ``{}`` if missing)."""
        if self._test_categories is None:
            path = os.path.join(self.project_root, self.TEST_CATEGORIES_FILENAME)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self._test_categories = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._test_categories = {}
        return self._test_categories

    def _iter_module_maps(self) -> Iterable[Tuple[str, Dict[str, Any]]]:
        """Yield ``(relative_directory, parsed_map_json)`` for each
        module map under the project root.
        """
        if self._module_maps is None:
            collected: List[Tuple[str, Dict[str, Any]]] = []
            for cur, dirs, files in os.walk(self.project_root):
                dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]
                if self.MAP_FILENAME not in files:
                    continue
                map_path = os.path.join(cur, self.MAP_FILENAME)
                try:
                    with open(map_path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    # Best-effort: a corrupt cache is not a query-time
                    # error, the next builder run will rewrite it.
                    continue
                rel_dir = self._rel(cur)
                collected.append((rel_dir, data))
            # Stable ordering for deterministic output.
            collected.sort(key=lambda item: item[0])
            self._module_maps = collected
        return self._module_maps

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rel(self, path: str) -> str:
        """Project-relative path with forward slashes, no leading './'."""
        try:
            rel = os.path.relpath(path, self.project_root)
        except ValueError:
            rel = path
        rel = rel.replace(os.sep, "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if rel == ".":
            return ""
        return rel

    @staticmethod
    def _first_line(doc: Optional[str]) -> Optional[str]:
        if not doc:
            return None
        for line in doc.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return None

    # ------------------------------------------------------------------
    # Public API — one method per RPC
    # ------------------------------------------------------------------

    def find_symbol(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the symbol record from ``agent_symbols.json``.

        The record carries ``file`` plus whichever of ``kind``, ``params``,
        ``doc``, ``role`` the indexer captured. When ``agent_tests.json``
        is present and has a non-null entry for this symbol, the result
        also gains a ``test`` field.

        ``None`` if the symbol is unknown.
        """
        symbols = self._load_symbols()
        entry = symbols.get(name)
        if entry is None:
            return None
        # Return a shallow copy so callers can't mutate the cache.
        out = dict(entry)
        # Fold in the test record (Feature B). Best-effort — when the
        # tests artifact is missing we just don't add the field.
        tests = self._load_tests()
        test_entry = tests.get(name)
        if test_entry:
            out["test"] = test_entry
        return out

    # Legacy umbrella roles — when a caller asks for an old name we
    # union the new, more specific buckets so older queries keep
    # working. Members include the umbrella itself so symbols still
    # tagged with it (e.g. fallback ``aiogram-handler`` for non-message
    # event types) aren't lost.
    _ROLE_UMBRELLAS: Dict[str, set] = {
        "aiogram-handler": {
            "aiogram-handler",
            "callback-handler",
            "command-handler",
            "fsm-message-handler",
            "text-match-handler",
            "catch-all-handler",
        },
    }

    def find_by_role(self, role: str) -> List[str]:
        """Return all symbol names tagged with ``role``.

        Roles live in ``agent_root.json.roles`` (e.g. ``webhook``,
        ``route``, ``migration``, ``scheduler-job``, ...). Returns an
        empty list when the role is unknown.

        Legacy umbrella names (e.g. ``aiogram-handler``) expand to the
        union of the more specific subroles introduced when the parser
        learned to split them — old call sites keep working.
        """
        root = self._load_root()
        roles = root.get("roles") or {}
        members = self._ROLE_UMBRELLAS.get(role)
        if members is not None:
            seen: set = set()
            out: List[str] = []
            for member in members:
                for name in roles.get(member) or ():
                    if name not in seen:
                        seen.add(name)
                        out.append(name)
            out.sort()
            return out
        bucket = roles.get(role) or []
        return list(bucket)

    def who_calls(self, symbol: str) -> List[Dict[str, str]]:
        """Best-effort callers list for ``symbol``.

        Heuristic
        ---------
        Each ``_module_map.json`` records, per file, the own-package
        modules it depends on (``dependencies``). We:

        1. Look up ``symbol`` in ``agent_symbols.json`` to find the
           defining file.
        2. Map that file to its top-level package segment
           (``backend/routes/admin_routes.py`` → ``backend``).
        3. Return every file whose ``dependencies`` list contains either
           the symbol name itself or that package segment.

        This is a structural approximation, not a true call graph — it
        will over-report (any importer of the package looks like a
        caller) and under-report (a relative import that didn't make
        it into ``dependencies`` is invisible). Use it as a starting
        list, then confirm by reading the source.
        """
        symbol_entry = self.find_symbol(symbol)
        target_pkg: Optional[str] = None
        if symbol_entry and symbol_entry.get("file"):
            head = symbol_entry["file"].split("/", 1)[0]
            if head and head not in (symbol_entry["file"],):
                target_pkg = head

        index = self._build_reverse_index()
        seen: Dict[str, Dict[str, str]] = {}

        # Direct hits on the symbol name.
        for hit in index.get(symbol, []):
            seen[hit["file"]] = hit
        # Hits on the defining package — only when we know it.
        if target_pkg:
            for hit in index.get(target_pkg, []):
                # Don't list the defining file as its own caller.
                if symbol_entry and hit["file"] == symbol_entry.get("file"):
                    continue
                seen.setdefault(hit["file"], hit)

        return sorted(seen.values(), key=lambda r: r["file"])

    def summarise_module(self, folder: str) -> Optional[Dict[str, Any]]:
        """Return a tight summary of a folder's ``_module_map.json``.

        For each file in the folder we keep the export ``name``, ``kind``,
        ``role``, and the first line of ``doc``. ``params`` are stripped
        to keep the payload small — call ``find_symbol`` if you need a
        signature.

        Returns ``None`` when the folder has no module map.
        """
        normalised = folder.replace("\\", "/").strip("/")
        # Allow leading "./" or "/" or absolute paths inside project_root.
        if os.path.isabs(normalised):
            normalised = self._rel(normalised)
        target = os.path.join(self.project_root, normalised) if normalised else self.project_root
        map_path = os.path.join(target, self.MAP_FILENAME)
        if not os.path.exists(map_path):
            return None
        try:
            with open(map_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

        slim_files: Dict[str, Any] = {}
        for fname, fdata in (data.get("files") or {}).items():
            if not isinstance(fdata, dict):
                slim_files[fname] = fdata
                continue
            slim_exports = []
            for exp in fdata.get("exports") or []:
                if not isinstance(exp, dict):
                    slim_exports.append(exp)
                    continue
                slim = {"name": exp.get("name"), "kind": exp.get("kind")}
                role = exp.get("role")
                if role:
                    slim["role"] = role
                first = self._first_line(exp.get("doc"))
                if first:
                    slim["doc"] = first
                slim_exports.append(slim)
            slim_files[fname] = {
                "exports": slim_exports,
                "dependencies": fdata.get("dependencies") or [],
            }

        return {
            "directory": data.get("directory") or normalised or ".",
            "files": slim_files,
        }

    def list_roles(self) -> Dict[str, int]:
        """``role → count`` map across the whole project.

        Synthetic umbrella counts (e.g. ``aiogram-handler``) are added
        on top of the raw subrole counts so an agent grep'ing for
        "how many aiogram handlers" still finds the answer with one
        lookup.
        """
        root = self._load_root()
        roles = root.get("roles") or {}
        out: Dict[str, int] = {r: len(names) for r, names in roles.items()}
        for umbrella, members in self._ROLE_UMBRELLAS.items():
            seen: set = set()
            for m in members:
                for n in roles.get(m) or ():
                    seen.add(n)
            if seen:
                # Always overwrite with the synthetic count — an existing
                # raw bucket under the umbrella name (legacy fallback
                # tags) is a strict subset of the union, so the synthetic
                # count is the right answer.
                out[umbrella] = len(seen)
        return out

    def list_modules(self) -> List[str]:
        """All scanned module folders, in the order recorded by the builder."""
        root = self._load_root()
        return list(root.get("modules") or [])

    # ------------------------------------------------------------------
    # Feature A — convention linter
    # ------------------------------------------------------------------

    def lint_violations(self) -> List[Dict[str, Any]]:
        """Run the convention linter (``.vc-context/conventions.json``).

        Empty list when the config file is missing — the linter is
        opt-in. See ``conventions.py`` for the rule schema and the
        supported rule kinds.
        """
        # Local import keeps the engine import-cheap when the linter
        # isn't used.
        from conventions import lint_project  # type: ignore[import-not-found]
        return lint_project(self.project_root)

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
        from test_linking import find_test_for_symbol  # type: ignore[import-not-found]
        return find_test_for_symbol(self.project_root, symbol,
                                    symbol_entry.get("file") or "")

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
        ordered: Dict[str, Dict[str, int]] = {
            r: role_buckets[r] for r in sorted(role_buckets)
        }
        ordered["overall"] = overall
        return ordered

    # ------------------------------------------------------------------
    # Feature G — coverage by role (one-tool surface for QA gaps)
    # ------------------------------------------------------------------

    def coverage_for_role(self, role: Optional[str] = None) -> Dict[str, Any]:
        """Test-coverage view, scoped or whole-project.

        ``role=None`` →

            {
              "roles":  {"<role>": {"total": ..., "with_test": ...,
                                    "coverage_pct": ...}, ...},
              "overall": {"total": ..., "with_test": ...,
                          "coverage_pct": ...}
            }

        ``role="<name>"`` (also accepts legacy umbrellas like
        ``"aiogram-handler"``) →

            {
              "role": "<name>",
              "total": ..., "with_test": ..., "coverage_pct": ...,
              "missing":  [{"name", "file"}, ...],   # symbols WITHOUT a test
              "covered":  [{"name", "file", "test_file", "test_function"}, ...]
            }

        Returns ``{"role": role, "total": 0, ...}`` (empty buckets) for
        unknown roles instead of raising — callers can present "no
        symbols found" without special-casing.
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
                covered.append({
                    "name": name,
                    "file": file,
                    "test_file": test.get("test_file"),
                    "test_function": test.get("test_function"),
                })
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

    def _symbols_get(self, name: str) -> Optional[Dict[str, Any]]:  # noqa: D401
        symbols = self._load_symbols()
        entry = symbols.get(name)
        return dict(entry) if isinstance(entry, dict) else None

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
    # Feature D — aiogram callback_data resolver
    # ------------------------------------------------------------------

    def find_callback(self, data: str) -> List[Dict[str, Any]]:
        """Resolve an aiogram ``callback_data`` string to its handler(s).

        Tries an exact lookup first, then falls back to the longest
        matching ``startswith`` prefix. Empty list when nothing matches
        or the index is missing.
        """
        from callback_index import find_callback as _find  # type: ignore[import-not-found]
        return _find(self._load_callbacks(), data)

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

    # ------------------------------------------------------------------
    # Feature H — test categorisation (unit / integration / unknown)
    # ------------------------------------------------------------------

    def classify_tests(self) -> Dict[str, Any]:
        """Return ``{summary, files}`` for the whole test suite.

        ``summary`` is ``{category → count}``; ``files`` is the raw
        ``{rel_path → {category, signals}}`` map. Empty containers when
        the artifact is missing.
        """
        from test_classifier import category_summary  # type: ignore[import-not-found]
        index = self._load_test_categories()
        return {
            "summary": category_summary(index),
            "files": index,
        }

    def tests_by_category(self, category: str) -> List[str]:
        """File paths for ``category`` (``"unit"`` / ``"integration"`` /
        ``"unknown"``). Sorted, deduped, empty list on miss."""
        from test_classifier import lookup_tests_by_category as _by  # type: ignore[import-not-found]
        return _by(self._load_test_categories(), category)

    # ------------------------------------------------------------------
    # Feature I — generic call-site lookup + log-line resolver
    # ------------------------------------------------------------------

    def find_call_sites(
        self,
        callable_name: str,
        match_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Live AST scan: every ``Call(...)`` site whose target matches
        ``callable_name`` (plain ``"foo"`` or dotted ``"x.y"``).

        Optional ``match_path`` is an fnmatch glob to restrict the
        scan (``"services/**"`` etc.). On-demand — no cached artifact.
        """
        from call_sites import find_call_sites as _find  # type: ignore[import-not-found]
        return _find(self.project_root, callable_name, match_path)

    def logline_to_symbol(self, line: str) -> Dict[str, Any]:
        """Parse a Python ``logging`` line and resolve to a
        ``{level, logger, file, message, symbol?, symbol_file?, role?}``
        record. ``matched=False`` when the line shape isn't recognised.
        """
        from logline_parser import logline_to_symbol as _resolve  # type: ignore[import-not-found]
        # Pass loaded symbols (or empty dict if missing) so the parser
        # can fold in symbol info when the message leads with a known
        # identifier. The loader caches.
        symbols = self._load_symbols() if os.path.isfile(
            os.path.join(self.project_root, self.SYMBOLS_FILENAME)
        ) else {}
        return _resolve(self.project_root, line, symbols=symbols)

    # ------------------------------------------------------------------
    # Internal: reverse-dependency index for who_calls
    # ------------------------------------------------------------------

    def _build_reverse_index(self) -> Dict[str, List[Dict[str, str]]]:
        """Walk every module map once, return ``token → [callers]``.

        ``token`` is anything that appears in a file's ``dependencies``
        list (an own-package name, sometimes a symbol). Callers are
        ``{file, kind}`` records — ``kind`` taken from the first export
        of that file when present, otherwise ``"file"``.
        """
        if self._reverse_deps is not None:
            return self._reverse_deps

        index: Dict[str, List[Dict[str, str]]] = {}
        for rel_dir, data in self._iter_module_maps():
            base = data.get("directory") or rel_dir or ""
            base = base.replace("\\", "/")
            while base.startswith("./"):
                base = base[2:]
            for fname, fdata in (data.get("files") or {}).items():
                if not isinstance(fdata, dict):
                    continue
                rel_file = f"{base}/{fname}" if base else fname
                rel_file = rel_file.lstrip("/")
                # Pick a kind hint from the first structured export.
                kind = "file"
                for exp in fdata.get("exports") or []:
                    if isinstance(exp, dict) and exp.get("kind"):
                        kind = exp["kind"]
                        break
                deps = fdata.get("dependencies") or []
                for dep in deps:
                    if not isinstance(dep, str):
                        continue
                    bucket = index.setdefault(dep, [])
                    if not any(b["file"] == rel_file for b in bucket):
                        bucket.append({"file": rel_file, "kind": kind})

        for bucket in index.values():
            bucket.sort(key=lambda r: r["file"])
        self._reverse_deps = index
        return index
