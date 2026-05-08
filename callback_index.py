"""Aiogram callback_data index.

Walks every Python file under ``project_root`` and collects every
``@router.callback_query(F.data == "...")`` /
``@router.callback_query(F.data.startswith("..."))`` /
``@router.callback_query(F.data.in_([...]))`` decorator we can spot.

The output (``agent_callbacks.json``) maps each callback string to a
list of records, so an agent answering "who handles this button?"
gets a one-step answer:

    {
      "adm:staff_add": [
        {"kind": "exact",  "handler": "adm_staff_add",
         "file": "bot/handlers/admin_staff.py", "line": 252}
      ],
      "adm:staff_detail:": [
        {"kind": "prefix", "handler": "adm_staff_detail",
         "file": "bot/handlers/admin_staff.py", "line": 189}
      ]
    }

Only ``exact``, ``prefix`` (``startswith``) and ``in_`` filters are
parsed. More exotic shapes (regexes, custom filter classes) are
ignored — they're rare and the index stays useful without them.

Stdlib only.
"""

from __future__ import annotations

import ast
import json
import os
from collections.abc import Iterable
from typing import Any, Dict, List, Optional, Tuple

CALLBACKS_FILENAME = "agent_callbacks.json"

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


# ----------------------------------------------------------------------
# AST helpers
# ----------------------------------------------------------------------


def _iter_python_files(project_root: str) -> Iterable[str]:
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(cur, f)


def _is_callback_query_decorator(dec: ast.AST) -> bool:
    """``True`` for ``@router.callback_query(...)`` / ``@dp.callback_query(...)``."""
    if not isinstance(dec, ast.Call):
        return False
    func = dec.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr != "callback_query":
        return False
    if not isinstance(func.value, ast.Name):
        return False
    return func.value.id in {"router", "dp"}


def _is_f_data(node: ast.AST) -> bool:
    """``True`` for the bare ``F.data`` expression."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "data"
        and isinstance(node.value, ast.Name)
        and node.value.id == "F"
    )


def _string_constant(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _string_list(node: ast.AST) -> Optional[List[str]]:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    out: List[str] = []
    for el in node.elts:
        s = _string_constant(el)
        if s is None:
            return None  # mixed/dynamic list — give up
        out.append(s)
    return out


def _extract_filter_records(arg: ast.AST) -> List[Tuple[str, str]]:
    """Return ``(kind, value)`` records produced by a single decorator arg.

    Recognises:
      * ``F.data == "x"``                → ("exact", "x")
      * ``F.data.startswith("x")``       → ("prefix", "x")
      * ``F.data.in_(["a","b"])``        → multiple ("exact", ...)

    Returns ``[]`` for anything else (custom filter, regex, dynamic
    expression). Multiple records are returned for ``in_`` so each
    string lands as its own entry in the index.
    """
    # F.data == "x"
    if isinstance(arg, ast.Compare):
        if (
            len(arg.ops) == 1
            and isinstance(arg.ops[0], ast.Eq)
            and _is_f_data(arg.left)
            and len(arg.comparators) == 1
        ):
            s = _string_constant(arg.comparators[0])
            if s is not None:
                return [("exact", s)]
        return []

    # F.data.startswith("x") / F.data.in_([...])
    if isinstance(arg, ast.Call):
        func = arg.func
        if not isinstance(func, ast.Attribute):
            return []
        if not _is_f_data(func.value):
            return []
        if func.attr == "startswith" and arg.args:
            s = _string_constant(arg.args[0])
            if s is not None:
                return [("prefix", s)]
        if func.attr == "in_" and arg.args:
            items = _string_list(arg.args[0])
            if items is not None:
                return [("exact", s) for s in items]

    return []


# ----------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------


def collect_callbacks(project_root: str) -> Dict[str, List[Dict[str, Any]]]:
    """Return ``{callback_data_string → [record, ...]}``.

    Each record: ``{kind, handler, file, line}``. ``kind`` is
    ``"exact"`` or ``"prefix"``.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}

    for full in _iter_python_files(project_root):
        try:
            with open(full, encoding="utf-8") as fh:
                source = fh.read()
        except OSError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        rel = os.path.relpath(full, project_root).replace(os.sep, "/")

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list or ():
                if not _is_callback_query_decorator(dec):
                    continue
                # Single positional arg in the common case; iterate
                # all so combined filters (rare) still register.
                for arg in dec.args:
                    for kind, value in _extract_filter_records(arg):
                        bucket = out.setdefault(value, [])
                        rec = {
                            "kind": kind,
                            "handler": node.name,
                            "file": rel,
                            "line": node.lineno,
                        }
                        # Dedupe — same handler may appear with the
                        # same filter twice if the file is re-included.
                        if rec not in bucket:
                            bucket.append(rec)

    # Stable order inside each bucket so artifacts diff cleanly.
    for bucket in out.values():
        bucket.sort(key=lambda r: (r["file"], r["line"], r["handler"]))
    return out


def write_callback_index(project_root: str, index: Dict[str, List[Dict[str, Any]]]) -> str:
    """Write ``agent_callbacks.json`` (sorted keys for deterministic builds)."""
    out_path = os.path.join(project_root, CALLBACKS_FILENAME)
    ordered = {k: index[k] for k in sorted(index)}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return out_path


# ----------------------------------------------------------------------
# Lookup
# ----------------------------------------------------------------------


def find_callback(
    index: Dict[str, List[Dict[str, Any]]],
    data: str,
) -> List[Dict[str, Any]]:
    """Resolve a runtime ``callback_data`` string against the index.

    Strategy:
      1. Exact key match → return its records (kind already ``exact``).
      2. Otherwise scan every ``prefix`` entry and pick the LONGEST
         prefix that ``data`` starts with — that's the most specific
         handler. Returns a single record (or many if several files
         share the same prefix).

    Returns ``[]`` when nothing matches.
    """
    if not data:
        return []

    exact = index.get(data) or []
    if exact:
        return [dict(r) for r in exact]

    longest: Optional[str] = None
    for key, records in index.items():
        if not records or records[0].get("kind") != "prefix":
            # The first record's kind is enough — collect_callbacks
            # never mixes ``exact`` and ``prefix`` under the same key
            # (different strings produce different keys).
            continue
        if data.startswith(key) and (longest is None or len(key) > len(longest)):
            longest = key

    if longest is None:
        return []
    return [dict(r) for r in index[longest]]
