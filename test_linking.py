"""Test-linking heuristic — pair each indexed symbol with its nearest test.

Heuristic for symbol ``X`` defined in ``path/to/foo.py``:

1. If a file ``tests/test_<basename>.py`` exists at the project root,
   open it and look for ``def test_*`` functions whose name contains
   ``X`` (case-insensitive substring match).
2. If found → return ``{test_file, test_function, line}``.
3. Otherwise → ``None``.

Output is dumped to ``agent_tests.json`` so the CLI / MCP layer can
read it with O(1) lookup.

Stdlib only.
"""

from __future__ import annotations

import ast
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


TESTS_FILENAME = "agent_tests.json"
DEFAULT_TESTS_DIR = "tests"


def _basename(file_path: str) -> str:
    """``a/b/foo.py`` → ``foo``."""
    name = os.path.basename(file_path)
    if name.endswith(".py"):
        name = name[:-3]
    return name


def _candidate_test_files(project_root: str, basename: str) -> List[str]:
    """All files named ``test_<basename>.py`` anywhere under ``tests/``.

    We support nested ``tests/`` subdirectories (e.g.
    ``tests/integration/test_foo.py``) by walking the tree.
    """
    target_name = f"test_{basename}.py"
    tests_dir = os.path.join(project_root, DEFAULT_TESTS_DIR)
    if not os.path.isdir(tests_dir):
        return []
    out: List[str] = []
    for cur, dirs, files in os.walk(tests_dir):
        # Don't descend into junk subtrees.
        dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git"}]
        if target_name in files:
            out.append(os.path.join(cur, target_name))
    return out


def _scan_test_functions(test_path: str) -> List[Tuple[str, int]]:
    """Return ``[(function_name, line)]`` for every ``test_*`` function.

    Uses AST so we only catch real definitions (not ``test_X = ...``
    or string mentions). Returns an empty list on parse failure.
    """
    try:
        with open(test_path, "r", encoding="utf-8") as fh:
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
    matches = [
        (name, line) for name, line in candidates
        if sym_lower in name.lower()
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: (len(item[0]), item[0]))
    return matches[0]


def find_test_for_symbol(
    project_root: str,
    symbol: str,
    file_path: str,
) -> Optional[Dict[str, Any]]:
    """Return ``{test_file, test_function, line}`` or ``None``.

    Pure function — used by the builder to populate
    ``agent_tests.json`` and by the CLI/MCP for ad-hoc lookups.
    """
    if not symbol or not file_path:
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
    """
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for name, entry in symbols.items():
        if not isinstance(entry, dict):
            out[name] = None
            continue
        file_path = entry.get("file") or ""
        out[name] = find_test_for_symbol(project_root, name, file_path)
    return out


def write_test_index(project_root: str, index: Dict[str, Any]) -> str:
    """Persist ``agent_tests.json`` and return its absolute path."""
    out_path = os.path.join(project_root, TESTS_FILENAME)
    ordered = {k: index[k] for k in sorted(index)}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return out_path
