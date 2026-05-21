"""Reverse call-site lookup — find every ``Call(...)`` whose target
matches a given name or attribute chain.

Use cases:

* ``find_call_sites("state.clear")`` — who clears aiogram FSM state.
* ``find_call_sites("session.commit")`` — every DB-write site.
* ``find_call_sites("redis.delete")`` — cache invalidations.
* ``find_call_sites("logger.error", match_path="services/**")`` — only
  error-emitters in a subtree.

Project-agnostic: nothing aiogram / FastAPI / SQLAlchemy specific
leaks into the AST matcher. The caller composes domain-aware queries.

On-demand scan, no persistent artifact: scanning a 200-file project
takes well under a second on cold disk, so a stale-vs-fresh index
adds no value.

Stdlib only.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import re
from collections.abc import Iterable
from typing import Any, Dict, List, Optional

IGNORE_DIRS = {
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
    ".ai-context",
    ".vc-context",
}


_TS_EXTS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})

# Heuristic: detect enclosing TS method/function by scanning forward.
# Matches: `function name(`, `async name(`, `methodName(` at start of line,
# `name = (` arrow, or class `name(` shorthand.
_TS_FN_OPEN = re.compile(
    r"(?:(?:async\s+)?function\s+(\w+)\s*\(|"
    r"(?:(?:public|private|protected|static|override|async)\s+)*(\w+)\s*\([^)]*\)\s*(?::\s*\S+\s*)?\s*\{|"
    r"(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*(?::\s*\S+\s*)?\s*=>)"
)


def _iter_python_files(project_root: str) -> Iterable[str]:
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(cur, f)


def _iter_ts_files(project_root: str) -> Iterable[str]:
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if os.path.splitext(f)[1].lower() in _TS_EXTS:
                yield os.path.join(cur, f)


def _find_ts_call_sites(
    project_root: str,
    callable_name: str,
    match_path: Optional[str] = None,
    include_tests: bool = False,
) -> List[Dict[str, Any]]:
    """Regex-based call-site finder for TypeScript/JavaScript.

    Matches:
    - ``name(`` / ``.name(`` — direct function/method calls
    - ``inject(Name)`` — Angular functional injection
    - ``: Name`` in constructor params — Angular constructor DI
    For dotted names like ``"service.launch"``, matches the full
    ``service.launch(`` chain as well as the bare ``launch(`` leaf so
    injected-service calls are found without knowing the variable name.
    """
    name_part = callable_name.split(".")[-1]
    call_re = re.compile(r"(?<!\w)" + re.escape(name_part) + r"\s*\(")
    # Angular functional injection: inject(ClassName) or inject(ClassName, ...)
    inject_re = re.compile(r"\binject\(\s*" + re.escape(name_part) + r"\b")
    # Constructor DI: private|public|protected|readonly varName: ClassName
    # Also bare `: ClassName` / `: ClassName,` / `: ClassName)` in ctor args.
    di_re = re.compile(
        r"(?:(?:private|public|protected|readonly)\s+\w+|@\w+\([^)]*\)\s*\w+)\s*:\s*"
        + re.escape(name_part)
        + r"\b"
    )

    out: List[Dict[str, Any]] = []
    for full in _iter_ts_files(project_root):
        rel = os.path.relpath(full, project_root).replace(os.sep, "/")
        if not include_tests and (".spec." in rel or ".test." in rel or "/tests/" in rel):
            continue
        if match_path and not fnmatch.fnmatch(rel, match_path):
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue

        current_fn = "<module>"
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            # Track enclosing function context (forward scan).
            fn_m = _TS_FN_OPEN.search(line)
            if fn_m:
                found = next((g for g in fn_m.groups() if g), None)
                if found:
                    current_fn = found
            # Skip imports, exports, and comment-only lines.
            if stripped.startswith(("import ", "export {", "//", "* ", "/*", " *")):
                continue
            matched_kind: Optional[str] = None
            if call_re.search(line):
                matched_kind = "call"
            elif inject_re.search(line):
                matched_kind = "inject"
            elif di_re.search(line):
                matched_kind = "di"
            if matched_kind:
                out.append(
                    {
                        "file": rel,
                        "line": lineno,
                        "function": current_fn,
                        "kind": matched_kind,
                        "raw": stripped[:120],
                    }
                )

    out.sort(key=lambda r: (r["file"], r["line"]))
    return out


def _build_parents(tree: ast.AST) -> Dict[int, ast.AST]:
    parents: Dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _enclosing_function(parents: Dict[int, ast.AST], node: ast.AST) -> Optional[str]:
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = parents.get(id(cur))
    return None


def _attr_chain(node: ast.AST) -> List[str]:
    """Walk an Attribute/Name chain, return ``["root", "mid", ..., "leaf"]``.

    ``foo.bar.baz`` → ``["foo", "bar", "baz"]``.
    ``foo()`` → ``["foo"]``.
    Dynamic / subscript receivers (``self.x[0].y``) yield the
    statically-resolvable suffix only — the ``[0].y`` portion gives
    ``["y"]``.
    """
    chain: List[str] = []
    cur: Optional[ast.AST] = node
    while isinstance(cur, ast.Attribute):
        chain.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        chain.append(cur.id)
    chain.reverse()
    return chain


def _matches(call: ast.Call, target_parts: List[str]) -> bool:
    """``True`` when this Call resolves to ``target_parts`` (dotted path)."""
    chain = _attr_chain(call.func)
    if not chain:
        return False
    n = len(target_parts)
    if len(target_parts) == 1:
        # Simple name: match if the LEAF identifier equals target.
        # Catches both ``foo()`` and ``anything.foo()``.
        return chain[-1] == target_parts[0]
    if len(chain) < n:
        return False
    return chain[-n:] == target_parts


def find_call_sites(
    project_root: str,
    callable_name: str,
    match_path: Optional[str] = None,
    include_tests: bool = False,
) -> List[Dict[str, Any]]:
    """Scan source files and return every call site where ``callable_name``
    is invoked.

    Auto-detects language from ``match_path`` extension or project layout:
    - ``*.py`` / ``services/**`` → Python AST (precise, zero false positives).
    - ``*.ts`` / ``src/**`` / ``*.tsx`` etc. → TypeScript regex scanner.
    - No ``match_path`` → scans both and merges results.

    Each record: ``{file, line, function, raw}``.
    ``callable_name`` accepts ``"foo"`` (any ``foo()`` / ``x.foo()``)
    or ``"x.foo"`` / ``"a.b.c"`` (full suffix match).
    """
    if not callable_name:
        return []

    # Determine which languages to scan.
    want_py = True
    want_ts = True
    if match_path:
        ext = os.path.splitext(match_path)[1].lower()
        if ext == ".py":
            want_ts = False
        elif ext in _TS_EXTS:
            want_py = False
        elif any(seg in match_path for seg in ("src/", "app/", "libs/", "projects/")):
            # Angular / TS project layout hints.
            want_py = False

    out: List[Dict[str, Any]] = []

    if want_py:
        target_parts = callable_name.split(".")
        for full in _iter_python_files(project_root):
            rel = os.path.relpath(full, project_root).replace(os.sep, "/")
            if not include_tests and rel.startswith("tests/"):
                continue
            if match_path and not fnmatch.fnmatch(rel, match_path):
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    source = fh.read()
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                continue
            parents = _build_parents(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not _matches(node, target_parts):
                    continue
                try:
                    raw = ast.unparse(node)
                except Exception:
                    raw = "?"
                if len(raw) > 120:
                    raw = raw[:117] + "..."
                fn = _enclosing_function(parents, node) or "<module>"
                out.append(
                    {
                        "file": rel,
                        "line": getattr(node, "lineno", 0),
                        "function": fn,
                        "raw": raw,
                    }
                )

    if want_ts:
        out.extend(
            _find_ts_call_sites(
                project_root,
                callable_name,
                match_path=match_path,
                include_tests=include_tests,
            )
        )

    out.sort(key=lambda r: (r["file"], r["line"]))
    return out
