"""Test-linking heuristic — pair each indexed symbol with its nearest test.

Two independent strategies, evaluated in order:

1. **Reference-based** (preferred). For every test file under
   ``tests/``, AST-walk each ``def test_*`` body and collect names
   that resolve to top-level imports — including ``patch("a.b.X")``
   string targets, since aiogram-style tests rely heavily on those.
   Reverse-index the result as ``{symbol → [(file, test_fn, line)]}``;
   the symbol's nearest test is the one with the shortest test_*
   name (most specific).

2. **Co-location fallback**. If reference-based linking misses,
   look for files matching ``test_<basename>*.py`` (prefix glob, not
   exact match — covers ``test_foo_handler.py`` for ``foo.py``) and
   substring-match symbol against test_* names.

For TypeScript / JavaScript projects (Angular, Vue, etc.), a parallel
path indexes ``**/*.spec.{ts,tsx,js,jsx,mjs,cjs}`` files via regex —
no AST, since stdlib has no JS parser. Imports + ``describe(...)`` /
``it(...)`` blocks become the "test_function" surface, mirroring the
Python contract. Co-location for TS spec files is by basename:
``cart.service.ts`` ↔ ``cart.service.spec.ts``.

Output is dumped to ``agent_tests.json`` so the CLI / MCP layer can
read it with O(1) lookup.

Stdlib only.
"""

from __future__ import annotations

import ast
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

TESTS_FILENAME = "agent_tests.json"
DEFAULT_TESTS_DIR = "tests"

# Walk-skip set for the TS spec scanner. Mirrors the agent_map
# defaults so we don't scan vendored test suites or build outputs.
_SPEC_IGNORE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "__pycache__",
        "dist",
        "dist_webpack",
        "build",
        ".venv",
        "venv",
        ".idea",
        ".vscode",
        ".ai-context",
        ".vc-context",
        "coverage",
        ".angular",
        ".cache",
        ".next",
        ".nuxt",
    }
)

_SPEC_EXTENSIONS = (".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx", ".spec.mjs", ".spec.cjs")


def _basename(file_path: str) -> str:
    """``a/b/foo.py`` → ``foo``."""
    name = os.path.basename(file_path)
    if name.endswith(".py"):
        name = name[:-3]
    return name


def _candidate_test_files(project_root: str, basename: str) -> List[str]:
    """All files matching ``test_<basename>*.py`` anywhere under ``tests/``.

    Prefix glob (not exact) so ``test_admin_staff_handler.py`` is a
    candidate for symbols defined in ``admin_staff.py``. We support
    nested ``tests/`` subdirectories (e.g.
    ``tests/integration/test_foo.py``) by walking the tree.
    """
    prefix = f"test_{basename}"
    tests_dir = os.path.join(project_root, DEFAULT_TESTS_DIR)
    if not os.path.isdir(tests_dir):
        return []
    out: List[str] = []
    for cur, dirs, files in os.walk(tests_dir):
        # Don't descend into junk subtrees.
        dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git"}]
        for fname in files:
            if fname.startswith(prefix) and fname.endswith(".py"):
                # Tighten the prefix: ``test_foo_bar`` should not match
                # ``test_foo.py`` — but ``test_foo_handler.py`` should
                # match ``foo``. So allow either an exact match or a
                # next char that's a separator-ish (``_`` or ``.``).
                tail = fname[len(prefix) :]
                if tail == ".py" or tail.startswith("_") or tail.startswith("."):
                    out.append(os.path.join(cur, fname))
    return out


def _scan_test_functions(test_path: str) -> List[Tuple[str, int]]:
    """Return ``[(function_name, line)]`` for every ``test_*`` function.

    Uses AST so we only catch real definitions (not ``test_X = ...``
    or string mentions). Returns an empty list on parse failure.
    """
    try:
        with open(test_path, encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: List[Tuple[str, int]] = []
    # We collect both top-level test_* functions and methods inside
    # `class Test*:` blocks — pytest accepts both styles.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                out.append((node.name, node.lineno))
    return out


def _best_match(symbol: str, candidates: List[Tuple[str, int]]) -> Optional[Tuple[str, int]]:
    """Pick the test whose name contains ``symbol`` (case-insensitive).

    Tie-breaker: shortest name wins (the most specific test —
    ``test_my_func`` beats ``test_my_func_with_something_extra``).
    """
    sym_lower = symbol.lower()
    if not sym_lower:
        return None
    matches = [(name, line) for name, line in candidates if sym_lower in name.lower()]
    if not matches:
        return None
    matches.sort(key=lambda item: (len(item[0]), item[0]))
    return matches[0]


# ----------------------------------------------------------------------
# Reference-based linking (Phase 1 improvement)
# ----------------------------------------------------------------------


def _collect_imported_names(tree: ast.AST) -> set:
    """Top-level imports in a module — names visible to test_* bodies.

    Captures:
    * ``from M import a, b as c`` → {"a", "c"}
    * ``import M`` → {"M"}
    * ``import M.N`` → {"M"}  (the binding in the namespace)
    * ``import M as P`` → {"P"}
    """
    names: set = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names.add(alias.asname)
                else:
                    # `import a.b.c` binds top name "a" in the namespace.
                    names.add(alias.name.split(".", 1)[0])
    return names


def _references_in_function(fn_node: ast.AST, imported: set) -> set:
    """Names referenced inside ``fn_node``'s body that match imports.

    Three kinds of references count:
    * ``Name(id=X)`` — direct mention or call.
    * ``Attribute(value=Name(id=X))`` — ``X.something``.
    * ``Call(func=Name(id="patch"), args=[Constant("a.b.X")])`` —
      monkeypatch by dotted-path string. We pull off the last
      segment, since that's the symbol getting replaced.
    """
    out: set = set()
    for sub in ast.walk(fn_node):
        if isinstance(sub, ast.Name) and sub.id in imported:
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            base = sub.value
            if isinstance(base, ast.Name) and base.id in imported:
                out.add(base.id)
        elif isinstance(sub, ast.Call):
            func = sub.func
            # patch("a.b.symbol", ...) and patch.object(...) — the first
            # form binds the last segment of the dotted path.
            is_patch_call = (isinstance(func, ast.Name) and func.id == "patch") or (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "patch"
                and func.attr in {"dict", "object", "multiple"}
            )
            if is_patch_call and sub.args:
                first = sub.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    last = first.value.rsplit(".", 1)[-1]
                    if last:
                        # Reference doesn't need to be in `imported` —
                        # patch strings target symbols by full path,
                        # the local namespace doesn't bind them.
                        out.add(last)
    return out


def _scan_test_file_references(
    test_path: str,
) -> Dict[str, List[Tuple[str, int]]]:
    """Per ``def test_*`` in ``test_path``, list referenced symbol names.

    Returns ``{symbol_name: [(test_function_name, line), ...]}``. A
    symbol can show up under multiple test functions in the same file
    — they all count as candidate links.
    """
    try:
        with open(test_path, encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    imported = _collect_imported_names(tree)
    out: Dict[str, List[Tuple[str, int]]] = {}
    for node in ast.iter_child_nodes(tree):
        # Top-level test_* functions.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            for sym in _references_in_function(node, imported):
                out.setdefault(sym, []).append((node.name, node.lineno))
        # Methods inside class Test*: blocks.
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for member in node.body:
                if isinstance(
                    member, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and member.name.startswith("test_"):
                    for sym in _references_in_function(member, imported):
                        out.setdefault(sym, []).append((member.name, member.lineno))
    return out


def _walk_test_files(project_root: str) -> List[str]:
    """All ``tests/**/test_*.py`` files (excluding ``__init__.py``)."""
    tests_dir = os.path.join(project_root, DEFAULT_TESTS_DIR)
    if not os.path.isdir(tests_dir):
        return []
    out: List[str] = []
    for cur, dirs, files in os.walk(tests_dir):
        dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git"}]
        for fname in files:
            if fname.startswith("test_") and fname.endswith(".py"):
                out.append(os.path.join(cur, fname))
    return out


# ----------------------------------------------------------------------
# TS/JS spec linker (Angular convention: <name>.spec.ts co-located).
# ----------------------------------------------------------------------

# `import {A, B as C} from './x'` / `import D from 'lib'` / `import * as N from 'lib'`.
# Three captured forms:
#   group 1: braced names (`A, B as C`)
#   group 2: default-import name (`D`)
#   group 3: namespace-import name (`N`)
_RE_SPEC_IMPORT = re.compile(
    r"^\s*import\s+"
    r"(?:"
    r"\{(?P<braced>[^}]+)\}"
    r"|"
    r"(?P<default>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?:,\s*\{(?P<after_default>[^}]+)\})?"
    r"|"
    r"\*\s+as\s+(?P<namespace>[A-Za-z_$][A-Za-z0-9_$]*)"
    r")\s+from\s+['\"]([^'\"]+)['\"]",
    re.M,
)
_RE_SPEC_DESCRIBE = re.compile(
    r"\bdescribe\s*\(\s*['\"`](?P<text>[^'\"`]+)['\"`]\s*,",
)
_RE_SPEC_IT = re.compile(
    r"\b(?:it|test)\s*\(\s*['\"`](?P<text>[^'\"`]+)['\"`]\s*,",
)


def _candidate_spec_files(project_root: str, file_path: str) -> List[str]:
    """Co-located ``<basename>.spec.<ext>`` next to a TS/JS source file.

    Mirrors the Angular convention where ``cart.service.ts`` has its
    spec at ``cart.service.spec.ts`` in the same directory. Returns
    every existing spec extension match (most projects only have one).
    """
    if not file_path:
        return []
    abs_src = os.path.join(project_root, file_path) if not os.path.isabs(file_path) else file_path
    src_dir = os.path.dirname(abs_src)
    src_name = os.path.basename(abs_src)
    # Strip the source extension to get the basename ("cart.service" from "cart.service.ts").
    for src_ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        if src_name.endswith(src_ext):
            stem = src_name[: -len(src_ext)]
            break
    else:
        return []
    out: List[str] = []
    for spec_ext in _SPEC_EXTENSIONS:
        candidate = os.path.join(src_dir, stem + spec_ext)
        if os.path.isfile(candidate):
            out.append(candidate)
    return out


def _imported_names_from_spec(content: str) -> set:
    """Collect imported binding names from a TS/JS spec file's source.

    Handles `{A, B as C}`, default `D`, default-with-named `D, {E}`,
    and namespace `* as N`. Returns the set of locally bound names.
    """
    names: set = set()
    for m in _RE_SPEC_IMPORT.finditer(content):
        for clause in (m.group("braced"), m.group("after_default")):
            if clause:
                for piece in clause.split(","):
                    piece = piece.strip()
                    if not piece:
                        continue
                    # Handle `B as C` — bind under the alias.
                    if " as " in piece:
                        _, alias = piece.split(" as ", 1)
                        alias = alias.strip()
                        if alias:
                            names.add(alias)
                    else:
                        names.add(piece)
        if m.group("default"):
            names.add(m.group("default"))
        if m.group("namespace"):
            names.add(m.group("namespace"))
    return names


def _spec_blocks(content: str) -> List[Tuple[str, int]]:
    """Return ``[(label, line)]`` for every describe/it block in source.

    Label format: ``<describe> :: <it>`` when the it() is enclosed in a
    describe(), else just the it() text. We don't try to parse nesting
    fully — pick the most-recent describe seen above each it().
    """
    out: List[Tuple[str, int]] = []
    # Index every describe/it occurrence by source offset.
    desc_hits = list(_RE_SPEC_DESCRIBE.finditer(content))
    it_hits = list(_RE_SPEC_IT.finditer(content))
    line_starts = _line_offsets(content)

    def line_of(offset: int) -> int:
        # Binary search would be cleaner, but specs rarely exceed a
        # few thousand lines so linear is fine.
        i = 0
        for j, start in enumerate(line_starts):
            if start > offset:
                break
            i = j
        return i + 1  # 1-indexed

    for it_m in it_hits:
        # Most-recent describe BEFORE this it.
        recent_desc = None
        for d in desc_hits:
            if d.start() < it_m.start():
                recent_desc = d
            else:
                break
        it_text = it_m.group("text").strip()
        if recent_desc is not None:
            label = f"{recent_desc.group('text').strip()} :: {it_text}"
        else:
            label = it_text
        out.append((label, line_of(it_m.start())))
    return out


def _line_offsets(content: str) -> List[int]:
    """Return the byte offset of each line start (line 1 → offsets[0])."""
    out = [0]
    for i, ch in enumerate(content):
        if ch == "\n":
            out.append(i + 1)
    return out


def _walk_spec_files(project_root: str) -> List[str]:
    """All ``**/*.spec.{ts,tsx,js,jsx,mjs,cjs}`` files, skipping
    vendored / build dirs (``node_modules``, ``dist``, etc.)."""
    if not os.path.isdir(project_root):
        return []
    out: List[str] = []
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in _SPEC_IGNORE_DIRS]
        for fname in files:
            if fname.endswith(_SPEC_EXTENSIONS):
                out.append(os.path.join(cur, fname))
    return out


def _scan_spec_file_references(
    spec_path: str,
) -> Dict[str, List[Tuple[str, int]]]:
    """Per spec file, list referenced symbol names → ``[(label, line)]``.

    Reference resolution: we treat every imported binding as a candidate
    symbol-under-test, and pair it with each describe/it block in the
    file. This is coarse — a spec usually imports one or two SUTs and
    a handful of fixtures — but the downstream "shortest label wins"
    tie-breaker keeps the result usable.
    """
    try:
        with open(spec_path, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return {}
    imported = _imported_names_from_spec(content)
    if not imported:
        return {}
    blocks = _spec_blocks(content)
    if not blocks:
        # Fall back to a single placeholder so the spec still links —
        # even a spec without an it() block tells you "the SUT has tests".
        blocks = [("(spec)", 1)]
    out: Dict[str, List[Tuple[str, int]]] = {}
    for sym in imported:
        for label, line in blocks:
            out.setdefault(sym, []).append((label, line))
    return out


def build_spec_reference_index(
    project_root: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Reverse index ``{symbol → [{test_file, test_function, line}, ...]}``
    sourced from ``**/*.spec.ts`` (and friends).

    Same shape as :func:`build_reference_index` so the two indexes can
    be merged transparently downstream.
    """
    index: Dict[str, List[Dict[str, Any]]] = {}
    for spec_path in _walk_spec_files(project_root):
        rel = os.path.relpath(spec_path, project_root).replace(os.sep, "/")
        per_file = _scan_spec_file_references(spec_path)
        for sym, hits in per_file.items():
            for label, line in hits:
                index.setdefault(sym, []).append(
                    {
                        "test_file": rel,
                        "test_function": label,
                        "line": line,
                    }
                )
    return index


def build_reference_index(
    project_root: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Reverse index ``{symbol → [{test_file, test_function, line}, ...]}``.

    Walks every ``tests/**/test_*.py`` once. Linear in the number of
    test files; the per-symbol lookup downstream is O(1).
    """
    index: Dict[str, List[Dict[str, Any]]] = {}
    for test_path in _walk_test_files(project_root):
        rel = os.path.relpath(test_path, project_root).replace(os.sep, "/")
        per_file = _scan_test_file_references(test_path)
        for sym, hits in per_file.items():
            for fn_name, line in hits:
                index.setdefault(sym, []).append(
                    {
                        "test_file": rel,
                        "test_function": fn_name,
                        "line": line,
                    }
                )
    return index


def _pick_best(
    hits: List[Dict[str, Any]],
    prefer_file: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Choose the most-specific hit. Ranking:

    1. ``prefer_file`` match (co-location bonus).
    2. Shortest ``test_function`` name (most specific to the symbol).
    3. Lexicographic on test_function for stable output.
    """
    if not hits:
        return None

    def key(h: Dict[str, Any]) -> Tuple[int, int, str]:
        co_located = 0 if (prefer_file and h["test_file"] == prefer_file) else 1
        return (co_located, len(h["test_function"]), h["test_function"])

    return sorted(hits, key=key)[0]


def find_test_for_symbol(
    project_root: str,
    symbol: str,
    file_path: str,
    reference_index: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    """Return ``{test_file, test_function, line}`` or ``None``.

    Pure function — used by the builder to populate
    ``agent_tests.json`` and by the CLI/MCP for ad-hoc lookups.

    When ``reference_index`` is provided (built once via
    :func:`build_reference_index`), it's consulted first; the
    co-location fallback only runs when no reference hit exists.
    """
    if not symbol:
        return None

    # Reference index — primary path.
    if reference_index and symbol in reference_index:
        # Co-location bonus: prefer hits in test_<basename>*.py files.
        prefer = None
        if file_path:
            basename = _basename(file_path)
            if basename:
                for cand in _candidate_test_files(project_root, basename):
                    rel = os.path.relpath(cand, project_root).replace(os.sep, "/")
                    prefer = rel
                    break
        best = _pick_best(reference_index[symbol], prefer)
        if best is not None:
            return best

    # Co-location fallback for Python: tests/test_<basename>*.py.
    if not file_path:
        return None
    basename = _basename(file_path)
    if not basename:
        return None
    for candidate in _candidate_test_files(project_root, basename):
        funcs = _scan_test_functions(candidate)
        match = _best_match(symbol, funcs)
        if match is None:
            continue
        rel = os.path.relpath(candidate, project_root).replace(os.sep, "/")
        return {
            "test_file": rel,
            "test_function": match[0],
            "line": match[1],
        }

    # Co-location fallback for TS/JS: <name>.spec.ts next to <name>.ts.
    # Triggers when reference_index didn't resolve (e.g. only ng-component
    # / ng-service classes with co-located specs that import via ./path).
    for candidate in _candidate_spec_files(project_root, file_path):
        per_file = _scan_spec_file_references(candidate)
        # The symbol may not appear in imports — fall back to the first
        # describe/it block as a coarse "this spec exists" signal.
        rel = os.path.relpath(candidate, project_root).replace(os.sep, "/")
        if per_file.get(symbol):
            label, line = per_file[symbol][0]
            return {"test_file": rel, "test_function": label, "line": line}
        try:
            with open(candidate, encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue
        blocks = _spec_blocks(content)
        if blocks:
            label, line = blocks[0]
            return {"test_file": rel, "test_function": label, "line": line}
        return {"test_file": rel, "test_function": "(spec)", "line": 1}

    return None


# ----------------------------------------------------------------------
# Build artifact
# ----------------------------------------------------------------------


def build_test_index(
    project_root: str,
    symbols: Dict[str, Dict[str, Any]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Build the ``{symbol → entry|null}`` map for ``agent_tests.json``.

    ``symbols`` is the loaded ``agent_symbols.json`` content. Symbols
    without a test get ``null`` so the surface stays uniform — agents
    can ask ``find_test(X)`` and never get a 404.

    The reference index is built once and reused across every symbol
    lookup, so the whole pass is O(test_files + symbols).
    """
    reference_index = build_reference_index(project_root)
    spec_index = build_spec_reference_index(project_root)
    # Merge: spec_index entries land in the same dict, so a single
    # symbol can have hits from both Python tests and TS specs (rare
    # in practice — a name is usually unique to one language tree).
    for sym, hits in spec_index.items():
        reference_index.setdefault(sym, []).extend(hits)
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for name, entry in symbols.items():
        if not isinstance(entry, dict):
            out[name] = None
            continue
        file_path = entry.get("file") or ""
        out[name] = find_test_for_symbol(
            project_root,
            name,
            file_path,
            reference_index=reference_index,
        )
    return out


def write_test_index(project_root: str, index: Dict[str, Any]) -> str:
    """Persist ``agent_tests.json`` and return its absolute path."""
    out_path = os.path.join(project_root, TESTS_FILENAME)
    ordered = {k: index[k] for k in sorted(index)}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return out_path
