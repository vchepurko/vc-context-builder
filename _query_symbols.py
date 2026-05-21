"""Symbol-query mixin — keep `query_engine.py` from sprawling.

Moved out of `QueryEngine`'s body because they share one shape:
every method here is rooted in ``agent_symbols.json`` / ``agent_root.json``
and answers "facts about a symbol" — definition, callers, callees,
decorators, raised exceptions, role membership, class shape, body
slice. Sibling mixins handle locales/lint (`_InspectorsMixin`),
routes (`_RoutesMixin`), tests (`_TestsMixin`).

Public surface stays unchanged — `QueryEngine(_QuerySymbolsMixin, ...)`
exposes the methods exactly as before.

Mixin contract: assumes the host class provides
``self.project_root: str``, ``self._reverse_deps`` cache slot, the
``SYMBOLS_FILENAME`` class constant, and the lazy loaders
``self._load_symbols()`` / ``self._load_root()`` /
``self._load_tests()`` / ``self._iter_module_maps()``.
No state of its own beyond the class constants below.
"""

from __future__ import annotations

import os
import re
from typing import Any, ClassVar, Dict, Iterable, List, Optional, Tuple

from _test_filter import filter_test_records, is_test_path
from paths import index_read_path as _index_read


class _QuerySymbolsMixin:
    """Symbol facts: find / inspect / who-calls / verify / role membership.

    The detailed contracts live on each method's docstring. Grouping
    here mirrors the order they had on ``QueryEngine`` before the split.
    """

    # ------------------------------------------------------------------
    # Type stubs — concrete values come from QueryEngine.__init__ /
    # its sibling mixins. Declared here so mypy / IDEs are quiet.
    # ------------------------------------------------------------------

    project_root: str
    _reverse_deps: Optional[Dict[str, List[Dict[str, str]]]]
    SYMBOLS_FILENAME: ClassVar[str]

    def _load_symbols(self) -> Dict[str, Dict[str, Any]]:
        """Provided by the host class; declared here so mypy is quiet."""
        raise NotImplementedError  # pragma: no cover — overridden by host

    def _load_root(self) -> Dict[str, Any]:
        """Provided by the host class; declared here so mypy is quiet."""
        raise NotImplementedError  # pragma: no cover — overridden by host

    def _load_tests(self) -> Dict[str, Any]:
        """Provided by the host class; declared here so mypy is quiet."""
        raise NotImplementedError  # pragma: no cover — overridden by host

    def _iter_module_maps(self) -> Iterable[Tuple[str, Dict[str, Any]]]:
        """Provided by the host class; declared here so mypy is quiet."""
        raise NotImplementedError  # pragma: no cover — overridden by host

    # ------------------------------------------------------------------
    # Default-hidden symbol fields. Exposed via `find_symbol`'s
    # whitelist or the dedicated `get_callees` / `get_raised_exceptions`
    # tools — keep the typical response under the token-economy band.
    # ------------------------------------------------------------------

    HIDE_BY_DEFAULT = ("callees", "raises", "decorators")

    # Defaults for the include_body slicer — chosen so a typical class
    # body fits without needing a follow-up Read, but large blobs
    # (huge React components, generated code) are clipped.
    BODY_SNIPPET_LINES = 200
    BODY_SNIPPET_MAX_BYTES = 8000

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

    # Allowed verify() kinds — kept as a class constant so the dispatcher
    # / spec can validate without re-importing the engine module.
    VERIFY_KINDS: ClassVar[Tuple[str, ...]] = (
        "exists",
        "calls",
        "decorated",
        "raises",
    )

    # ------------------------------------------------------------------
    # find_symbol / find_symbols — primary symbol lookup
    # ------------------------------------------------------------------

    def find_symbol(
        self,
        name: str,
        *,
        fields: Optional[List[str]] = None,
        include_body: bool = False,
        include_tests: bool = False,
    ) -> Optional[Dict[str, Any]]:
        # Project-level override via .vc-context/conventions.json:
        #   { "find_symbol_include_body": true }
        # Allows opting in without changing every call site.
        if not include_body and hasattr(self, "_read_convention"):
            include_body = bool(self._read_convention("find_symbol_include_body", False))
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
            # Case-insensitive fallback — handles camelCase mismatches and
            # lowercase queries like "collectionPlayerStateService".
            lower = name.lower()
            for k, v in symbols.items():
                if k.lower() == lower:
                    entry = v
                    break
        if entry is None and name.startswith("I") and len(name) > 1 and name[1].isupper():
            # Strip leading I for interface names: IMyService → MyService.
            entry = symbols.get(name[1:])
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

    # ------------------------------------------------------------------
    # Symbol-fact accessors — callees / decorators / raises / verify
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Composite: symbol card / role membership / callers
    # ------------------------------------------------------------------

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

        # Direct hits on the symbol name in deps index.
        for hit in index.get(symbol, []):
            seen[hit["file"]] = hit

        # Language-aware caller lookup:
        # - TypeScript/JS: grep import lines for symbol name (precise).
        #   Package-level heuristic (target_pkg='src') is too broad for
        #   Angular projects — any file importing from 'src/' would match.
        # - Python: use package-level reverse-dep index (fast, good enough).
        if symbol_entry and symbol_entry.get("file", "").endswith((".ts", ".tsx", ".js", ".jsx")):
            for hit in self._find_ts_importers(symbol, include_tests=include_tests):
                seen.setdefault(hit["file"], hit)
        else:
            if target_pkg:
                for hit in index.get(target_pkg, []):
                    if symbol_entry and hit["file"] == symbol_entry.get("file"):
                        continue
                    seen.setdefault(hit["file"], hit)

        callers = sorted(seen.values(), key=lambda r: r["file"])
        return filter_test_records(callers, include_tests=include_tests)

    # ------------------------------------------------------------------
    # Live-scan: call sites + class shape
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

    def inspect_class(self, name: str) -> Optional[Dict[str, Any]]:
        """Resolve a class by name and return its structured summary
        (file, line, doc, bases, fields, methods). ``None`` for unknown
        names or non-class symbols.

        Cross-language:
          * Python classes — full AST walk via ``class_inspector``
            (SQLAlchemy models, pydantic schemas, dataclasses, plain
            classes).
          * TypeScript classes — regex-based extraction adapted to the
            same shape: ``bases`` from ``extends`` / ``implements``,
            ``fields`` from ``@Input`` / ``@Output`` / ``constructor``
            DI params (kind tag distinguishes them), ``methods`` from
            public method signatures. Replaces 3-4 manual
            ``read_slice`` calls per Angular component audit.
        """
        from class_inspector import inspect_class as _inspect  # type: ignore[import-not-found]

        symbols = (
            self._load_symbols()
            if os.path.isfile(_index_read(self.project_root, self.SYMBOLS_FILENAME))
            else {}
        )
        result = _inspect(self.project_root, name, symbols=symbols)
        if result is not None:
            return result
        # Fall-through for TS / TSX classes — Python AST returns None,
        # but ``ng_ts_class_shape`` can pull the same conceptual shape.
        entry = symbols.get(name) or {}
        file_rel = entry.get("file")
        if not isinstance(file_rel, str) or not file_rel.endswith((".ts", ".tsx")):
            return None
        if entry.get("kind") != "class":
            return None
        shape = self.ng_ts_class_shape(name)  # type: ignore[attr-defined]
        if shape is None:
            return None
        # Translate Angular-flavoured fields into inspect_class shape.
        fields: List[Dict[str, Any]] = []
        for p in shape.get("constructor_params") or []:
            fields.append(
                {
                    "name": p.get("name"),
                    "type": p.get("type"),
                    "default": None,
                    "kind": "ctor-param",
                }
            )
        for inp in shape.get("inputs") or []:
            fields.append({"name": inp, "type": None, "default": None, "kind": "input"})
        for out in shape.get("outputs") or []:
            fields.append({"name": out, "type": None, "default": None, "kind": "output"})
        methods = [
            {"name": m, "kind": "func", "params": None, "doc": None}
            for m in shape.get("public_methods") or []
        ]
        bases: List[str] = []
        try:
            with open(os.path.join(self.project_root, file_rel), encoding="utf-8") as fh:
                src = fh.read()
            cls_re = re.compile(
                r"(?:^|\n)\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?"
                r"class\s+" + re.escape(name) + r"\b(?P<heritage>[^{]*)\{"
            )
            m = cls_re.search(src)
            if m:
                heritage = m.group("heritage")
                ext = re.search(r"extends\s+([^\s,{<]+(?:<[^>]*>)?)", heritage)
                if ext:
                    bases.append(ext.group(1).strip())
                impl = re.search(r"implements\s+([^{]+)", heritage)
                if impl:
                    for b in impl.group(1).split(","):
                        b = b.strip()
                        if b:
                            bases.append(b)
        except OSError:
            pass
        return {
            "name": name,
            "file": file_rel,
            "line": entry.get("line"),
            "doc": entry.get("doc"),
            "bases": bases,
            "fields": fields,
            "methods": methods,
        }

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

    _TS_IGNORE_DIRS = frozenset({
        ".git", "node_modules", "vendor", "__pycache__", "dist", "build",
        ".venv", "venv", ".ai-context", ".vc-context",
    })
    _TS_EXTS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs"})

    def _find_ts_importers(
        self,
        symbol: str,
        *,
        include_tests: bool = False,
    ) -> List[Dict[str, str]]:
        """Grep all TS/JS files for ``import ... SymbolName ...`` lines.

        Returns ``[{file, kind}]`` — much more precise than the
        package-level heuristic for Angular/TS projects where a broad
        top-level dir like ``src`` matches every consumer.
        """
        import_re = re.compile(r"\bimport\b[^;]*\b" + re.escape(symbol) + r"\b")
        results: List[Dict[str, str]] = []
        for cur, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in self._TS_IGNORE_DIRS]
            for fname in files:
                if os.path.splitext(fname)[1].lower() not in self._TS_EXTS:
                    continue
                full = os.path.join(cur, fname)
                rel = os.path.relpath(full, self.project_root).replace(os.sep, "/")
                if not include_tests and (".spec." in rel or ".test." in rel):
                    continue
                try:
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            if import_re.search(line):
                                results.append({"file": rel, "kind": "file"})
                                break
                except OSError:
                    continue
        results.sort(key=lambda r: r["file"])
        return results

    # ------------------------------------------------------------------
    # Internal: cheap symbol-record getter (used by other mixins)
    # ------------------------------------------------------------------

    def _symbols_get(self, name: str) -> Optional[Dict[str, Any]]:
        symbols = self._load_symbols()
        entry = symbols.get(name)
        return dict(entry) if isinstance(entry, dict) else None
