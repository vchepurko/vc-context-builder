"""Anti-pattern detectors registry.

Layered on top of the existing AST walkers, this module hosts a small
set of "if you see this in the project, something is broken" rules.
Each detector is a plain function ``detect(project_root) -> list``
that returns ``[{rule, file, line, function?, evidence}]`` records.

The MCP surface exposes them via ``find_anti_patterns(rule_name)``
and ``list_anti_patterns()``. Adding a rule: register a callable in
``_DETECTORS`` and document the trigger / evidence shape.

Stdlib only.
"""

from __future__ import annotations

import ast
import os
from typing import Any, Callable, Dict, List

# Directories the convention linter / callback indexer also skip — keep
# the surface in sync so a detector doesn't trip on venv'd stdlib.
_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".github",
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
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)


def _iter_python_files(project_root: str):
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(cur, f)


# ----------------------------------------------------------------------
# Detector 1: aiogram-state-check-in-body
# ----------------------------------------------------------------------


def _is_router_message_decorator(dec: ast.AST) -> bool:
    """``True`` for ``@router.message(...)`` / ``@dp.message(...)`` —
    the aiogram pattern guarded by CLAUDE.md against bare F-filters."""
    if not isinstance(dec, ast.Call):
        return False
    func = dec.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr != "message":
        return False
    if not isinstance(func.value, ast.Name):
        return False
    return func.value.id in {"router", "dp"}


def _has_f_expression(node: ast.AST) -> bool:
    """True when ``node`` carries an ``F.<...>`` attribute access —
    the magic filter object aiogram exposes."""
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "F"
        ):
            return True
    return False


def _references_non_f_name(node: ast.AST) -> bool:
    """True when ``node`` references at least one ``ast.Name`` whose
    id is not ``F`` — heuristic for "this argument carries a state
    filter / a Command() / a custom filter class".
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id != "F":
            return True
    return False


def detect_aiogram_state_check_in_body(project_root: str) -> List[Dict[str, Any]]:
    """Find ``@router.message(F.<anything>)`` decorators with no state
    filter — the silent-dispatch killer pinned in
    ``CLAUDE.md`` ("aiogram pitfalls"). Once `F` matches, aiogram
    stops dispatch even if the body short-circuits via
    ``state.get_state()``; sibling state-bound handlers never get a
    chance to fire.

    Scope: any Python file under ``bot/handlers/**`` (the canonical
    location). Other files are skipped to keep the signal aimed at
    real handlers.

    Each record: ``{rule, file, line, function, evidence}``, sorted by
    ``(file, line)``.
    """
    rule = "aiogram-state-check-in-body"
    out: List[Dict[str, Any]] = []
    for full in _iter_python_files(project_root):
        rel = os.path.relpath(full, project_root).replace(os.sep, "/")
        if not (rel.startswith("bot/handlers/") or "/bot/handlers/" in f"/{rel}"):
            continue
        try:
            with open(full, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list or ():
                if not _is_router_message_decorator(dec):
                    continue
                args = dec.args if isinstance(dec, ast.Call) else []
                if not any(_has_f_expression(a) for a in args):
                    continue
                if any(_references_non_f_name(a) for a in args):
                    continue
                out.append(
                    {
                        "rule": rule,
                        "file": rel,
                        "line": getattr(dec, "lineno", node.lineno),
                        "function": node.name,
                        "evidence": (
                            "@router.message(F.<...>) without a state-filter argument "
                            "— bare F.text/F.data matches first, dispatch stops, "
                            "sibling state-bound handlers never receive the update"
                        ),
                    }
                )
    out.sort(key=lambda r: (r["file"], r["line"]))
    return out


# ----------------------------------------------------------------------
# Registry + dispatcher
# ----------------------------------------------------------------------


_DETECTORS: Dict[str, Callable[[str], List[Dict[str, Any]]]] = {
    "aiogram-state-check-in-body": detect_aiogram_state_check_in_body,
}


def list_anti_patterns() -> List[str]:
    """All registered rule names, sorted."""
    return sorted(_DETECTORS)


def find_anti_patterns(project_root: str, rule: str) -> List[Dict[str, Any]]:
    """Run one registered detector. Returns ``[]`` for unknown rule
    names (caller can compare against :func:`list_anti_patterns`).
    """
    detector = _DETECTORS.get(rule)
    if detector is None:
        return []
    return detector(project_root)
