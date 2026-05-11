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
import re
from collections.abc import Iterable
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from _query_inspectors import _InspectorsMixin
from _query_routes import _RoutesMixin
from _query_tests import _TestsMixin
from _test_filter import filter_test_records, is_test_path


class QueryEngine(_InspectorsMixin, _RoutesMixin, _TestsMixin):
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
    NG_ROUTES_FILENAME = "agent_ng_routes.json"
    CALLBACKS_FILENAME = "agent_callbacks.json"
    FSM_FLOW_FILENAME = "agent_fsm_flows.json"
    TEST_CATEGORIES_FILENAME = "agent_test_categories.json"
    LOCALES_FILENAME = "agent_locale_keys.json"
    MAP_FILENAME = "_module_map.json"
    IGNORE_DIRS = frozenset(
        {
            ".git",
            "node_modules",
            "vendor",
            "__pycache__",
            "dist",
            "build",
            ".venv",
            "venv",
            ".idea",
            ".vscode",
        }
    )

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
        self._ng_routes: Optional[List[Dict[str, Any]]] = None
        self._callbacks: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._fsm_flows: Optional[Dict[str, Dict[str, Any]]] = None
        self._test_categories: Optional[Dict[str, Dict[str, Any]]] = None
        self._locale_keys: Optional[Dict[str, Dict[str, Any]]] = None

    # ------------------------------------------------------------------
    # Cache invalidation — used after an in-process rebuild so the next
    # query reads fresh artifacts. The engine doesn't watch the disk
    # for changes; callers explicitly drop caches when they've rebuilt
    # the index (e.g. via ``rebuild_index`` MCP tool).
    # ------------------------------------------------------------------

    def invalidate_caches(self) -> None:
        """Drop every lazy-load cache. Forces the next query to re-read
        ``agent_*.json`` from disk."""
        self._root = None
        self._symbols = None
        self._module_maps = None
        self._reverse_deps = None
        self._tests = None
        self._routes = None
        self._ng_routes = None
        self._callbacks = None
        self._fsm_flows = None
        self._test_categories = None
        self._locale_keys = None

    # ------------------------------------------------------------------
    # Lazy loaders
    # ------------------------------------------------------------------

    def _load_root(self) -> Dict[str, Any]:
        if self._root is None:
            path = os.path.join(self.project_root, self.ROOT_FILENAME)
            with open(path, encoding="utf-8") as fh:
                self._root = json.load(fh)
        return self._root

    def _load_symbols(self) -> Dict[str, Dict[str, Any]]:
        if self._symbols is None:
            path = os.path.join(self.project_root, self.SYMBOLS_FILENAME)
            with open(path, encoding="utf-8") as fh:
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
                with open(path, encoding="utf-8") as fh:
                    self._tests = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._tests = {}
        return self._tests

    def _load_routes(self) -> Dict[str, Dict[str, Any]]:
        """Return ``agent_routes.json`` content (or ``{}`` if missing)."""
        if self._routes is None:
            path = os.path.join(self.project_root, self.ROUTES_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._routes = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._routes = {}
        return self._routes

    def _load_ng_routes(self) -> List[Dict[str, Any]]:
        """Return ``agent_ng_routes.json`` content (or ``[]`` if missing)."""
        if self._ng_routes is None:
            path = os.path.join(self.project_root, self.NG_ROUTES_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                self._ng_routes = data if isinstance(data, list) else []
            except (OSError, json.JSONDecodeError):
                self._ng_routes = []
        return self._ng_routes

    def _load_callbacks(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return ``agent_callbacks.json`` content (or ``{}`` if missing).

        Same graceful-degradation contract as the other optional
        artifact loaders — Feature D is fresh and may be absent on
        older builds.
        """
        if self._callbacks is None:
            path = os.path.join(self.project_root, self.CALLBACKS_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._callbacks = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._callbacks = {}
        return self._callbacks

    def _load_fsm_flows(self) -> Dict[str, Dict[str, Any]]:
        """Return ``agent_fsm_flows.json`` content (or ``{}`` if missing)."""
        if self._fsm_flows is None:
            path = os.path.join(self.project_root, self.FSM_FLOW_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._fsm_flows = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._fsm_flows = {}
        return self._fsm_flows

    def _load_test_categories(self) -> Dict[str, Dict[str, Any]]:
        """Return ``agent_test_categories.json`` (or ``{}`` if missing)."""
        if self._test_categories is None:
            path = os.path.join(self.project_root, self.TEST_CATEGORIES_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._test_categories = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._test_categories = {}
        return self._test_categories

    def _load_locale_keys(self) -> Dict[str, Dict[str, Any]]:
        """Return ``agent_locale_keys.json`` (or ``{}`` if missing)."""
        if self._locale_keys is None:
            path = os.path.join(self.project_root, self.LOCALES_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._locale_keys = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._locale_keys = {}
        return self._locale_keys

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
                    with open(map_path, encoding="utf-8") as fh:
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

    # Fact fields hidden from the default `find_symbol` response so
    # large lists (callees especially) don't bloat the lean shape.
    # Always available via explicit ``fields=`` whitelist or via the
    # dedicated `get_callees` / `get_raised_exceptions` tools.
    HIDE_BY_DEFAULT = ("callees", "raises", "decorators")

    def find_symbol(
        self,
        name: str,
        *,
        fields: Optional[List[str]] = None,
        include_body: bool = False,
        include_tests: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Return the symbol record from ``agent_symbols.json``.

        Parameters
        ----------
        name:
            Symbol name (case-sensitive).
        fields:
            Whitelist of keys to keep in the response.  Defaults to the
            full record minus ``HIDE_BY_DEFAULT`` (the "fact" fields
            that can balloon — see ``get_callees`` /
            ``get_raised_exceptions`` for those).  Pass ``["file"]``
            for a 30-token "where is X?" answer; pass
            ``["file", "callees"]`` to opt back into a fact field.
            Unknown keys are dropped silently.
        include_body:
            When ``True``, also embed a ``body`` field with the
            verbatim source slice for the symbol (Python: AST
            ``get_source_segment``; JS/TS: ``BODY_SNIPPET_LINES``
            lines starting at the indexed ``line``).  Saves a follow-up
            ``Read`` of the file when the caller already knows it
            wants the body.  Capped at ``BODY_SNIPPET_MAX_BYTES``.
        include_tests:
            By default symbols declared under ``tests/`` are hidden — a
            test fixture / helper rarely answers "where is the
            production X defined?".  Set ``True`` when the caller
            specifically wants to find a test symbol (or check whether
            a name exists in tests at all).

        Returns ``None`` when the symbol is unknown OR when it lives in
        a test file and ``include_tests`` is False.
        """
        symbols = self._load_symbols()
        entry = symbols.get(name)
        if entry is None:
            return None
        if not include_tests and is_test_path(entry.get("file")):
            return None
        # Return a shallow copy so callers can't mutate the cache.
        out = dict(entry)
        # Fold in the test record (Feature B). Best-effort — when the
        # tests artifact is missing we just don't add the field.
        tests = self._load_tests()
        test_entry = tests.get(name)
        if test_entry:
            out["test"] = test_entry
        if include_body:
            body = self._extract_body(name, out)
            if body is not None:
                out["body"] = body
        if fields:
            allow = set(fields)
            out = {k: v for k, v in out.items() if k in allow}
        else:
            for hide in self.HIDE_BY_DEFAULT:
                out.pop(hide, None)
        return out

    def find_symbols(
        self,
        names: List[str],
        *,
        fields: Optional[List[str]] = None,
        include_body: bool = False,
        include_tests: bool = False,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """Batch wrapper — N lookups in one MCP round-trip.

        Three lookups via ``find_symbol`` cost ~3 × 135 ≈ 400 tokens
        of round-trip overhead; a single ``find_symbols`` call carries
        the same payload at ~150 tokens because schema + envelope are
        shared.  Use when the caller needs more than one symbol at a
        time (sibling handlers, dependency chains, etc.).

        ``include_tests`` matches the singular ``find_symbol`` — test-
        path symbols are hidden by default; pass ``True`` to surface
        them.

        Names not in the index map to ``null`` so the dict stays
        keyed by request order.
        """
        out: Dict[str, Optional[Dict[str, Any]]] = {}
        for name in names:
            out[name] = self.find_symbol(
                name,
                fields=fields,
                include_body=include_body,
                include_tests=include_tests,
            )
        return out

    # Defaults for the include_body slicer — chosen so a typical class
    # body fits without needing a follow-up Read, but large blobs
    # (huge React components, generated code) are clipped.
    BODY_SNIPPET_LINES = 200
    BODY_SNIPPET_MAX_BYTES = 8000

    def _extract_body(
        self,
        name: str,
        record: Dict[str, Any],
    ) -> Optional[str]:
        """Return the verbatim source slice for a symbol record, or
        ``None`` when the file isn't available.

        Python: re-parses the file with ``ast`` so the slice respects
        decorators / class scope.  JS/TS/anything else: a regex
        locates the declaration line, then a line-based slice capped
        at ``BODY_SNIPPET_LINES`` lines / ``BODY_SNIPPET_MAX_BYTES``
        bytes.

        ``name`` is passed separately because ``agent_symbols.json``
        keys symbols by name; the record dict itself doesn't carry it.
        """
        rel = record.get("file")
        if not isinstance(rel, str) or not rel:
            return None
        abs_path = os.path.join(self.project_root, rel)
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as fh:
                source = fh.read()
        except OSError:
            return None
        target = name
        if not isinstance(target, str) or not target:
            return None
        if rel.endswith(".py"):
            import ast

            try:
                tree = ast.parse(source)
            except SyntaxError:
                return None
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and node.name == target
                ):
                    seg = ast.get_source_segment(source, node)
                    if seg is None:
                        continue
                    if len(seg.encode("utf-8")) > self.BODY_SNIPPET_MAX_BYTES:
                        # Fall through to line-trim path so the response
                        # stays bounded even on huge classes.
                        seg = seg[: self.BODY_SNIPPET_MAX_BYTES] + "\n# … truncated …"
                    return seg
            return None
        # JS / TS: agent_symbols.json doesn't carry a line for these
        # yet, so locate the declaration via regex on the file body.
        # Patterns cover the common shapes the TS parser already
        # detects (class / function / const = ).  Cheap; the
        # ``BODY_SNIPPET_LINES`` cap bounds the worst case.
        import re

        decl_re = re.compile(
            r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?"
            r"(?:abstract\s+)?(?:class|function|const|let|var|interface|type|enum)\s+"
            + re.escape(target)
            + r"\b",
            re.M,
        )
        m = decl_re.search(source)
        if m is None:
            return None
        # Walk back to start of the line for stable formatting.
        start_offset = source.rfind("\n", 0, m.start()) + 1
        line_start = source[:start_offset].count("\n")
        lines = source.splitlines()
        snippet = "\n".join(lines[line_start : line_start + self.BODY_SNIPPET_LINES])
        if len(snippet.encode("utf-8")) > self.BODY_SNIPPET_MAX_BYTES:
            snippet = snippet[: self.BODY_SNIPPET_MAX_BYTES] + "\n// … truncated …"
        return snippet

    # Legacy umbrella roles — when a caller asks for an old name we
    # union the new, more specific buckets so older queries keep
    # working. Members include the umbrella itself so symbols still
    # tagged with it (e.g. fallback ``aiogram-handler`` for non-message
    # event types) aren't lost.
    _ROLE_UMBRELLAS: ClassVar[Dict[str, set]] = {
        "aiogram-handler": {
            "aiogram-handler",
            "callback-handler",
            "command-handler",
            "fsm-message-handler",
            "text-match-handler",
            "catch-all-handler",
        },
    }

    def get_callees(self, symbol: str) -> List[str]:
        """Return identifiers this symbol calls in its body.

        Sourced from the AST walk during indexing — bare ``foo()``
        emits ``foo``; attribute chains ``a.b.c()`` emit the rightmost
        attribute (``c``).  Sorted, deduplicated.  ``[]`` when the
        symbol is unknown OR has no calls (constants, empty stubs).

        Token economy: stays out of the default `find_symbol` response
        (see ``HIDE_BY_DEFAULT``) — call this when you actually need
        the call graph.  Pair with ``find_symbol`` on each name to map
        a symbol's downstream dependencies in 2 round-trips.
        """
        symbols = self._load_symbols()
        entry = symbols.get(symbol) or {}
        callees = entry.get("callees") or []
        return list(callees) if isinstance(callees, list) else []

    def get_decorated_with(
        self,
        decorator: str,
        *,
        include_tests: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return symbols whose ``decorators`` list contains
        ``decorator``.

        Match is *suffix-aware* — passing ``"router.get"`` matches
        ``router.get``; passing ``"get"`` matches ``router.get`` /
        ``app.get`` / bare ``get`` because decorator-name semantics
        cross attribute chains.  Pass the full path
        (e.g. ``"app.middleware"``) to disambiguate.

        Each item: ``{name, file, line, kind, role?}``.  Empty list
        when nothing matches OR the index pre-dates ``decorators``
        capture (run ``python3 .ai-context/agent_map.py``).

        ``include_tests`` defaults to False — symbols from test files
        rarely matter for "where in the app is this decorator used?".
        Set True if specifically auditing test fixtures.

        Generalisation of ``find_by_role`` — works for ANY decorator
        the indexer saw, not just the role-mapped ones.
        """
        target = decorator.strip()
        if not target:
            return []
        symbols = self._load_symbols()
        out: List[Dict[str, Any]] = []
        for name, entry in symbols.items():
            decs = entry.get("decorators") or []
            if not isinstance(decs, list):
                continue
            for d in decs:
                if d == target or d.endswith("." + target):
                    rec: Dict[str, Any] = {
                        "name": name,
                        "file": entry.get("file"),
                        "line": entry.get("line"),
                        "kind": entry.get("kind"),
                    }
                    role = entry.get("role")
                    if role:
                        rec["role"] = role
                    out.append(rec)
                    break
        out = filter_test_records(out, include_tests=include_tests)
        out.sort(key=lambda r: (r.get("file") or "", r.get("line") or 0))
        return out

    def get_raised_exceptions(self, symbol: str) -> List[str]:
        """Return exception class names this symbol raises.

        ``raise ValueError(...)`` → ``"ValueError"``;
        ``raise pkg.HTTPError(...)`` → ``"HTTPError"`` (rightmost
        attribute, no module resolution).  Bare ``raise`` (re-raise)
        contributes nothing.  Sorted, deduplicated.

        Use as a fact-check: claims like "this handler raises X" are
        verifiable in one MCP call instead of reading the source.
        """
        symbols = self._load_symbols()
        entry = symbols.get(symbol) or {}
        raises = entry.get("raises") or []
        return list(raises) if isinstance(raises, list) else []

    # Allowed verify() kinds — kept as a class constant so the dispatcher
    # / spec can validate without re-importing the engine module.
    VERIFY_KINDS: ClassVar[Tuple[str, ...]] = (
        "exists",
        "calls",
        "decorated",
        "raises",
    )

    def verify(
        self,
        kind: str,
        subject: str,
        target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Typed fact-check primitive — answer one of four yes/no
        questions about the symbol index without reading source code.

        Kinds and their semantics::

            verify("exists",    "Foo")            → does symbol Foo exist?
            verify("calls",     "Foo", "bar")     → does Foo call bar?
            verify("decorated", "Foo", "cached")  → is Foo decorated @cached?
            verify("raises",    "Foo", "ValueError") → does Foo raise ValueError?

        Returns ``{kind, subject, target?, result, evidence}`` where
        ``result`` is a bool and ``evidence`` is a short string the
        agent can quote when it asserts the fact.

        Unknown ``kind`` returns ``result=False, evidence="unknown
        verify kind"``. Use as a one-call alternative to
        ``find_symbol`` + ``get_callees`` + ``in`` checks scattered
        across the agent's reasoning chain.
        """
        if kind not in self.VERIFY_KINDS:
            return {
                "kind": kind,
                "subject": subject,
                "result": False,
                "evidence": (f"unknown verify kind: {kind!r} (allowed: {list(self.VERIFY_KINDS)})"),
            }
        subject = (subject or "").strip()
        if not subject:
            return {
                "kind": kind,
                "subject": subject,
                "result": False,
                "evidence": "empty subject",
            }

        if kind == "exists":
            entry = self._load_symbols().get(subject)
            ok = entry is not None
            return {
                "kind": kind,
                "subject": subject,
                "result": ok,
                "evidence": (
                    f"{subject} → {entry['file']}:{entry.get('line', '?')}"
                    if ok and isinstance(entry, dict)
                    else f"{subject} not in agent_symbols.json"
                ),
            }

        target = (target or "").strip()
        if not target:
            return {
                "kind": kind,
                "subject": subject,
                "result": False,
                "evidence": f"{kind!r} requires non-empty target",
            }

        if kind == "calls":
            callees = self.get_callees(subject)
            ok = target in callees
            return {
                "kind": kind,
                "subject": subject,
                "target": target,
                "result": ok,
                "evidence": (
                    f"{subject}.callees ∋ {target}"
                    if ok
                    else f"{subject}.callees has {len(callees)} entries, {target} absent"
                ),
            }

        if kind == "raises":
            raises = self.get_raised_exceptions(subject)
            ok = target in raises
            return {
                "kind": kind,
                "subject": subject,
                "target": target,
                "result": ok,
                "evidence": (
                    f"{subject}.raises ∋ {target}"
                    if ok
                    else f"{subject}.raises = {raises or '[]'}, {target} absent"
                ),
            }

        # kind == "decorated" — leverage suffix-aware match from
        # get_decorated_with so passing "post" matches "app.post".
        if kind == "decorated":
            entry = self._load_symbols().get(subject) or {}
            decs = entry.get("decorators") or []
            if not isinstance(decs, list):
                decs = []
            ok = any(d == target or d.endswith("." + target) for d in decs)
            return {
                "kind": kind,
                "subject": subject,
                "target": target,
                "result": ok,
                "evidence": (
                    f"{subject}.decorators ∋ {target} (matched in {decs!r})"
                    if ok
                    else f"{subject}.decorators = {decs or '[]'}, {target} absent"
                ),
            }

        # Defensive — should be unreachable thanks to the guard above.
        return {
            "kind": kind,
            "subject": subject,
            "result": False,
            "evidence": "fall-through (this shouldn't happen)",
        }

    # Defaults for read_slice — small enough to keep responses in the
    # token-economy band, large enough to fit a typical method body.
    SLICE_MAX_LINES = 200
    SLICE_MAX_BYTES = 8000

    def read_slice(
        self,
        file_path: str,
        start: int,
        end: int,
    ) -> Optional[Dict[str, Any]]:
        """Return ``{file, start, end, content, truncated}`` for a
        small range of a file relative to ``project_root``.

        ``start`` / ``end`` are 1-indexed inclusive line numbers.  The
        return value's ``content`` is a ``\\n``-joined slice (no line
        prefixes — the caller already knows the offsets).  Caps at
        ``SLICE_MAX_LINES`` lines or ``SLICE_MAX_BYTES`` bytes; sets
        ``truncated`` when either limit fired.

        Returns ``None`` when the path escapes ``project_root`` or
        when the file doesn't exist.  This is the consistent
        MCP-channel for source reads — paired with ``find_symbol``'s
        ``line`` / ``end_line`` it lets an agent jump straight to
        evidence without shelling out.
        """
        if not isinstance(start, int) or not isinstance(end, int):
            return None
        if start < 1 or end < start:
            return None
        # Resolve & containment-check.  ``os.path.commonpath`` is the
        # cleanest way to confirm the resolved abspath sits under root
        # without falling for ``..`` chains or symlink trickery.
        rel = file_path.replace(os.sep, "/").lstrip("/")
        abs_path = os.path.abspath(os.path.join(self.project_root, rel))
        try:
            common = os.path.commonpath([abs_path, os.path.abspath(self.project_root)])
        except ValueError:
            return None
        if common != os.path.abspath(self.project_root):
            return None
        if not os.path.isfile(abs_path):
            return None

        cap_end = min(end, start + self.SLICE_MAX_LINES - 1)
        truncated = cap_end < end
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as fh:
                collected: List[str] = []
                for lineno, raw in enumerate(fh, 1):
                    if lineno < start:
                        continue
                    if lineno > cap_end:
                        break
                    collected.append(raw.rstrip("\n"))
        except OSError:
            return None

        content = "\n".join(collected)
        if len(content.encode("utf-8")) > self.SLICE_MAX_BYTES:
            content = content.encode("utf-8")[: self.SLICE_MAX_BYTES].decode(
                "utf-8", errors="ignore"
            )
            truncated = True

        return {
            "file": rel,
            "start": start,
            "end": cap_end,
            "content": content,
            "truncated": truncated,
        }

    def get_symbol_card(self, symbol: str) -> Optional[Dict[str, Any]]:
        """One-call symbol overview: bundles ``find_symbol`` (with
        line range), ``get_callees``, ``get_raised_exceptions``,
        ``find_test``, and a compact callers summary into a single
        round-trip.

        Token economy: replaces the typical 4-call sequence
        (find_symbol → get_callees → get_raised_exceptions → who_calls
        → find_test) with one ~250-token response.  Use this when a
        playbook needs a full picture of a symbol before deciding what
        to read next.

        Returns ``None`` when the symbol is unknown.  Callers list is
        capped at 5 entries (full count in ``callers.total``) so big
        hub functions don't bloat the card.
        """
        symbols = self._load_symbols()
        entry = symbols.get(symbol)
        if entry is None:
            return None

        callees = entry.get("callees") or []
        raises = entry.get("raises") or []

        out: Dict[str, Any] = {
            "name": symbol,
            "file": entry.get("file"),
            "line": entry.get("line"),
            "end_line": entry.get("end_line"),
            "kind": entry.get("kind"),
            "params": entry.get("params"),
            "doc": entry.get("doc"),
            "role": entry.get("role"),
            "callees": list(callees) if isinstance(callees, list) else [],
            "raises": list(raises) if isinstance(raises, list) else [],
        }

        # Test linkage — best-effort.
        tests = self._load_tests()
        test_entry = tests.get(symbol)
        if test_entry:
            out["test"] = test_entry

        # Callers summary — capped, since hub symbols can have 50+.
        callers = self.who_calls(symbol)
        out["callers"] = {
            "total": len(callers),
            "top": callers[:5],
        }

        # Drop keys that didn't apply (None / empty) so the card stays
        # tight — callers/callees/raises always render even when empty
        # because their *shape* is part of the contract.
        keep_empty = {"name", "callees", "raises", "callers"}
        out = {k: v for k, v in out.items() if k in keep_empty or v}
        return out

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

    def who_calls(
        self,
        symbol: str,
        *,
        include_tests: bool = False,
    ) -> List[Dict[str, str]]:
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

        ``include_tests`` defaults to False — test files importing the
        symbol are noise for the "where is this used in the app?"
        question. Set True when the caller is auditing coverage.

        This is a structural approximation, not a true call graph — it
        will over-report (any importer of the package looks like a
        caller) and under-report (a relative import that didn't make
        it into ``dependencies`` is invisible). Use it as a starting
        list, then confirm by reading the source.
        """
        # Internal lookup must NOT honour include_tests — we still want
        # to know about test-defined symbols here so target_pkg resolves
        # for the package-level callers below.
        symbol_entry = self.find_symbol(symbol, include_tests=True)
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

        callers = sorted(seen.values(), key=lambda r: r["file"])
        return filter_test_records(callers, include_tests=include_tests)

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
            with open(map_path, encoding="utf-8") as fh:
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

    def repo_map(self) -> Dict[str, Any]:
        """Return a top-level project shape — module list with file
        counts, export counts, and dominant role per module.

        Built by walking every ``_module_map.json`` once.  The output
        is the cheapest way to ask "what does this project look like?"
        without iterating modules manually.

        Shape::

            {
              "modules": [
                {
                  "path": "./bot/handlers",
                  "files": 12,
                  "exports": 89,
                  "dominant_role": "aiogram-handler",
                  "roles": {"aiogram-handler": 67, "fsm-state": 4}
                },
                ...
              ],
              "totals": {"modules": 14, "files": 83, "exports": 412}
            }
        """
        modules: List[Dict[str, Any]] = []
        total_files = 0
        total_exports = 0
        for mp, data in self._iter_module_maps():
            files = data.get("files") or {}
            file_count = len(files)
            exports = 0
            roles: Dict[str, int] = {}
            for fdata in files.values():
                if not isinstance(fdata, dict):
                    continue
                for exp in fdata.get("exports") or []:
                    if not isinstance(exp, dict):
                        continue
                    exports += 1
                    r = exp.get("role")
                    if r:
                        roles[r] = roles.get(r, 0) + 1
            # `roles.get` confuses mypy's overloaded `max`; lambda
            # makes the key signature unambiguous.
            dominant = max(roles, key=lambda k: roles[k]) if roles else None
            modules.append(
                {
                    "path": data.get("directory") or os.path.dirname(self._rel(mp)),
                    "files": file_count,
                    "exports": exports,
                    "dominant_role": dominant,
                    "roles": roles,
                }
            )
            total_files += file_count
            total_exports += exports
        modules.sort(key=lambda m: m["path"])
        return {
            "modules": modules,
            "totals": {
                "modules": len(modules),
                "files": total_files,
                "exports": total_exports,
            },
        }

    def get_file_card(self, path: str) -> Optional[Dict[str, Any]]:
        """Return a single file's summary — exports, dependencies,
        dominant role.  Slim version of ``summarise_module`` scoped to
        one file.

        Pulls from the file's containing folder ``_module_map.json``.
        Each export shows ``name``, ``kind``, ``role``, ``line``,
        ``end_line`` (when indexed) plus the first line of its doc.
        Returns ``None`` when the file isn't in any module map.

        Use to answer "what does this file do?" without reading it.
        """
        rel = path.replace("\\", "/").strip("/")
        if os.path.isabs(rel):
            rel = self._rel(rel)
        if "/" in rel:
            folder, fname = rel.rsplit("/", 1)
        else:
            folder, fname = "", rel
        map_path = os.path.join(self.project_root, folder, self.MAP_FILENAME)
        if not os.path.exists(map_path):
            return None
        try:
            with open(map_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        files = data.get("files") or {}
        fdata = files.get(fname)
        if not isinstance(fdata, dict):
            return None

        slim_exports: List[Dict[str, Any]] = []
        roles_seen: Dict[str, int] = {}
        for exp in fdata.get("exports") or []:
            if not isinstance(exp, dict):
                continue
            slim: Dict[str, Any] = {"name": exp.get("name"), "kind": exp.get("kind")}
            for k in ("role", "line", "end_line"):
                v = exp.get(k)
                if v:
                    slim[k] = v
            first = self._first_line(exp.get("doc"))
            if first:
                slim["doc"] = first
            slim_exports.append(slim)
            r = exp.get("role")
            if r:
                roles_seen[r] = roles_seen.get(r, 0) + 1

        return {
            "file": rel,
            "exports": slim_exports,
            "dependencies": fdata.get("dependencies") or [],
            "roles": roles_seen,
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

    # find_test / coverage_stats / coverage_for_role / classify_tests /
    # tests_by_category — now provided by ``_TestsMixin``.

    def _symbols_get(self, name: str) -> Optional[Dict[str, Any]]:
        symbols = self._load_symbols()
        entry = symbols.get(name)
        return dict(entry) if isinstance(entry, dict) else None

    # find_route / route_callers / route_for_js_call / ng_list_routes /
    # ng_route_for_path / ng_routes_for_component / find_callback /
    # trace_fsm_flow — provided by ``_RoutesMixin``.
    # classify_tests / tests_by_category — provided by ``_TestsMixin``.

    # ------------------------------------------------------------------
    # Feature I — generic call-site lookup + log-line resolver
    # ------------------------------------------------------------------

    def find_call_sites(
        self,
        callable_name: str,
        match_path: Optional[str] = None,
        *,
        include_tests: bool = False,
    ) -> List[Dict[str, Any]]:
        """Live AST scan: every ``Call(...)`` site whose target matches
        ``callable_name`` (plain ``"foo"`` or dotted ``"x.y"``).

        Optional ``match_path`` is an fnmatch glob to restrict the
        scan (``"services/**"`` etc.). On-demand — no cached artifact.

        ``include_tests`` defaults to False — call sites in ``tests/``
        are usually noise for the "where is this function used?"
        question. Note: when ``match_path`` is already targeting
        ``tests/**`` the filter is a no-op (nothing left to drop).
        """
        from call_sites import find_call_sites as _find  # type: ignore[import-not-found]

        sites = _find(self.project_root, callable_name, match_path)
        return filter_test_records(sites, include_tests=include_tests)

    def logline_to_symbol(self, line: str) -> Dict[str, Any]:
        """Parse a Python ``logging`` line and resolve to a
        ``{level, logger, file, message, symbol?, symbol_file?, role?}``
        record. ``matched=False`` when the line shape isn't recognised.
        """
        from logline_parser import logline_to_symbol as _resolve  # type: ignore[import-not-found]

        # Pass loaded symbols (or empty dict if missing) so the parser
        # can fold in symbol info when the message leads with a known
        # identifier. The loader caches.
        symbols = (
            self._load_symbols()
            if os.path.isfile(os.path.join(self.project_root, self.SYMBOLS_FILENAME))
            else {}
        )
        return _resolve(self.project_root, line, symbols=symbols)

    # ------------------------------------------------------------------
    # Git bridge — symbols touched by current changes
    # ------------------------------------------------------------------

    _DIFF_HUNK_RE = re.compile(
        r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@",
    )

    def get_changed_symbols(
        self,
        base: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return symbols whose ``(file, line, end_line)`` overlap any
        hunk of ``git diff <base>..HEAD`` (default base: working tree
        — i.e. ``git diff`` for unstaged + staged).

        Each item: ``{name, file, line, end_line, kind, role?}``.
        Sorted by ``(file, line)`` for deterministic output.

        Use to scope a refactor review or a CI signal: "which symbols
        did this branch actually touch?"  Symbols whose def block is
        outside any hunk (e.g. a single-line comment edit inside a
        function still touches that function) are still returned —
        the function's full range overlaps.

        Returns ``[]`` when not in a git repo OR when the diff is
        empty.  Symbols not in the index (new files yet to be
        re-indexed) are silently dropped — call
        ``python3 .ai-context/agent_map.py`` and retry.
        """
        try:
            import subprocess  # local import — keeps import time low

            cmd = ["git", "diff", "--unified=0"]
            if base and base.strip():
                cmd.append(f"{base.strip()}..HEAD")
            proc = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []

        # Parse hunks: file -> [(start, end_inclusive), ...]
        hunks: Dict[str, List[tuple]] = {}
        current_file: Optional[str] = None
        for line in proc.stdout.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:].strip()
                continue
            if line.startswith("+++ "):
                # +++ /dev/null (file deleted) — no symbol overlap to record
                current_file = None
                continue
            if not line.startswith("@@") or current_file is None:
                continue
            m = self._DIFF_HUNK_RE.match(line)
            if not m:
                continue
            start = int(m.group(1))
            length = int(m.group(2) or 1)
            if length == 0:
                continue  # pure deletion in old side, no new lines
            hunks.setdefault(current_file, []).append(
                (start, start + length - 1),
            )

        if not hunks:
            return []

        symbols = self._load_symbols()
        out: List[Dict[str, Any]] = []
        for name, entry in symbols.items():
            file_v = entry.get("file")
            if not file_v or file_v not in hunks:
                continue
            sym_start = entry.get("line")
            sym_end = entry.get("end_line") or sym_start
            if not isinstance(sym_start, int) or not isinstance(sym_end, int):
                continue
            for h_start, h_end in hunks[file_v]:
                if h_end < sym_start or h_start > sym_end:
                    continue
                rec: Dict[str, Any] = {
                    "name": name,
                    "file": file_v,
                    "line": sym_start,
                    "end_line": sym_end,
                    "kind": entry.get("kind"),
                }
                role = entry.get("role")
                if role:
                    rec["role"] = role
                out.append(rec)
                break
        out.sort(key=lambda r: (r["file"], r["line"]))
        return out

    # ------------------------------------------------------------------
    # Feature J — whitelisted check runner (tests / lint / typecheck)
    # ------------------------------------------------------------------

    def list_checks(self) -> List[str]:
        """Available check names declared in
        ``.vc-context/conventions.json`` → ``checks``. Empty when
        the block is missing.
        """
        from checks import list_checks as _list  # type: ignore[import-not-found]

        return _list(self.project_root)

    def run_check(self, name: str, timeout_sec: Optional[int] = None) -> Dict[str, Any]:
        """Execute a whitelisted check by name. Returns
        ``{name, command, returncode, duration_ms, stdout_tail,
        stderr_tail, summary, error?}``. Refuses unknown names with
        returncode -2.
        """
        from checks import run_check as _run  # type: ignore[import-not-found]

        return _run(self.project_root, name, timeout_sec=timeout_sec)

    # ------------------------------------------------------------------
    # Feature L — class inspector (fields / methods / bases)
    # ------------------------------------------------------------------

    def inspect_class(self, name: str) -> Optional[Dict[str, Any]]:
        """Resolve a class by name and return its structured summary
        (file, line, doc, bases, fields, methods). ``None`` for unknown
        names or non-class symbols. Replaces grep-ing for class
        definitions when you need the field shape (SQLAlchemy models,
        pydantic schemas, dataclasses, plain classes).
        """
        from class_inspector import inspect_class as _inspect  # type: ignore[import-not-found]

        symbols = (
            self._load_symbols()
            if os.path.isfile(os.path.join(self.project_root, self.SYMBOLS_FILENAME))
            else {}
        )
        return _inspect(self.project_root, name, symbols=symbols)

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

    # ------------------------------------------------------------------
    # Template search
    # ------------------------------------------------------------------

    def find_in_templates(
        self,
        pattern: str,
        match_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search Angular HTML templates for *pattern* (case-insensitive).

        Walks every ``.html`` file under ``project_root``, skipping
        ``IGNORE_DIRS``.  Returns a list of
        ``{file, line, text}`` dicts, one per matching line, capped at
        100 results so the context window stays manageable.

        Parameters
        ----------
        pattern:
            Substring to look for (CSS class, Angular binding expression,
            selector tag, event handler, etc.).  Matched case-insensitively.
        match_path:
            Optional ``fnmatch``-style glob applied to the relative file path,
            e.g. ``"collection-player-v2/**"``.  Only files whose relative
            path matches are searched.
        """
        import fnmatch

        needle = pattern.lower()
        results: List[Dict[str, Any]] = []

        for dirpath, dirnames, filenames in os.walk(self.project_root):
            # Prune ignored dirs in-place so os.walk skips them entirely.
            dirnames[:] = [d for d in dirnames if d not in self.IGNORE_DIRS]
            for fname in filenames:
                if not fname.endswith(".html"):
                    continue
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, self.project_root).replace("\\", "/")
                if match_path and not fnmatch.fnmatch(rel_path, match_path):
                    continue
                try:
                    with open(abs_path, encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if needle in line.lower():
                                results.append(
                                    {
                                        "file": rel_path,
                                        "line": lineno,
                                        "text": line.rstrip(),
                                    }
                                )
                                if len(results) >= 100:
                                    return results
                except OSError:
                    continue

        results.sort(key=lambda r: (r["file"], r["line"]))
        return results

    # ------------------------------------------------------------------
    # Angular helpers (Feature P)
    # ------------------------------------------------------------------

    def ng_audit_component(self, name: str) -> Optional[Dict[str, Any]]:
        """Composite audit for one Angular @Component class.

        Returns a flattened record built from agent_symbols.json plus
        agent_tests.json — selector / templateUrl / standalone / inputs
        / outputs / styleUrls / nearest test. Returns ``None`` when the
        symbol is unknown or not an ng-component.
        """
        sym = self.find_symbol(name)
        if sym is None or sym.get("role") != "ng-component":
            return None
        test = self.find_test(name)
        return {
            "name": name,
            "file": sym.get("file"),
            "role": "ng-component",
            "selector": sym.get("ng_selector"),
            "template_url": sym.get("ng_template_url"),
            "style_urls": sym.get("ng_style_urls", []),
            "standalone": sym.get("ng_standalone"),
            "inputs": sym.get("inputs", []),
            "outputs": sym.get("outputs", []),
            "doc": sym.get("doc"),
            "test": test,
        }

    def ng_uses_selector(
        self,
        selector: str,
        match_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Find HTML templates referencing *selector* as either an
        element (``<selector``) or attribute directive (``[selector]``).

        Two passes through ``find_in_templates`` deduplicated by
        ``(file, line)``. Capped at 100 results.
        """
        if not selector:
            return []
        seen: set = set()
        out: List[Dict[str, Any]] = []
        for needle in (f"<{selector}", f"[{selector}]"):
            for hit in self.find_in_templates(needle, match_path=match_path):
                key = (hit["file"], hit["line"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(hit)
                if len(out) >= 100:
                    return out
        return out

    def ng_overview(self) -> Dict[str, Any]:
        """Zero-arg snapshot of the Angular surface area.

        Returns role counts plus a list of services with
        ``providedIn: 'root'`` and a count of standalone components.
        Cheap — single pass over agent_symbols.json.
        """
        symbols = self._load_symbols()
        roles = (
            "ng-component",
            "ng-service",
            "ng-module",
            "ng-pipe",
            "ng-directive",
            "ng-guard",
        )
        counts: Dict[str, int] = {role: 0 for role in roles}
        standalone = 0
        providers_root: List[str] = []
        for name, rec in symbols.items():
            role = rec.get("role")
            if role in counts:
                counts[role] += 1
            if role == "ng-component" and rec.get("ng_standalone") is True:
                standalone += 1
            if role == "ng-service" and rec.get("ng_provided_in") == "root":
                providers_root.append(name)
        providers_root.sort()
        return {
            "counts": counts,
            "standalone_components": standalone,
            "providers_root": providers_root,
        }

    def ng_inject_graph(self, service: str) -> List[Dict[str, Any]]:
        """Heuristic call sites for an Angular service injection.

        Two patterns are scanned across each module map's source
        files (regex on the unscrubbed body so the search is fast):

        * ``constructor(... : ServiceName)`` — classic DI.
        * ``inject(ServiceName)`` — Angular 14+ functional inject.

        Result is a list of ``{file, line, kind}`` (kind ∈
        ``constructor`` / ``inject``). Substring scan only — confirm
        by reading the source. Returns ``[]`` for unknown service.
        """
        if not service:
            return []
        import re as _re

        ctor_re = _re.compile(r":\s*" + _re.escape(service) + r"\b")
        inject_re = _re.compile(r"\binject\s*\(\s*" + _re.escape(service) + r"\s*[,\)]")

        out: List[Dict[str, Any]] = []
        for folder, mmap in self._iter_module_maps():
            for filename in mmap.get("files", {}):
                if not filename.endswith((".ts", ".tsx")):
                    continue
                rel = os.path.join(folder, filename).replace("\\", "/")
                abs_path = os.path.join(self.project_root, rel)
                try:
                    with open(abs_path, encoding="utf-8", errors="replace") as fh:
                        for lineno, raw in enumerate(fh, 1):
                            if ctor_re.search(raw):
                                out.append(
                                    {
                                        "file": rel,
                                        "line": lineno,
                                        "kind": "constructor",
                                    }
                                )
                            elif inject_re.search(raw):
                                out.append(
                                    {
                                        "file": rel,
                                        "line": lineno,
                                        "kind": "inject",
                                    }
                                )
                except OSError:
                    continue
                if len(out) >= 200:
                    return out
        return out
