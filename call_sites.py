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


def _iter_python_files(project_root: str) -> Iterable[str]:
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(cur, f)


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
) -> List[Dict[str, Any]]:
    """Scan every ``.py`` file under ``project_root`` and return the
    list of call sites where ``callable_name`` is invoked.

    Each record: ``{file, line, function, raw}``.
    * ``function`` — name of the enclosing function/coroutine, or
      ``"<module>"`` for module-level calls.
    * ``raw`` — first 120 chars of the call expression (``ast.unparse``).

    ``callable_name`` accepts ``"foo"`` (matches any ``foo()`` /
    ``x.foo()`` call), or ``"x.foo"`` / ``"a.b.c"`` (matches the full
    suffix). Empty input → empty list.

    ``match_path`` — optional fnmatch-style glob to restrict the scan
    (e.g. ``"services/**"`` or ``"bot/handlers/*.py"``).
    """
    if not callable_name:
        return []
    target_parts = callable_name.split(".")

    out: List[Dict[str, Any]] = []
    for full in _iter_python_files(project_root):
        rel = os.path.relpath(full, project_root).replace(os.sep, "/")
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

    out.sort(key=lambda r: (r["file"], r["line"]))
    return out
