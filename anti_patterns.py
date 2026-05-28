"""Anti-pattern detectors registry.

Layered on top of the existing AST walkers, this module hosts a small
set of "if you see this in the project, something is broken" rules.
Each detector is a plain function ``detect(project_root) -> list``
that returns ``[{rule, file, line, function?, evidence}]`` records.

The MCP surface exposes them via ``find_anti_patterns(rule_name)``
and ``list_anti_patterns()``. Adding a rule: register a callable in
``_DETECTORS`` and document the trigger / evidence shape.

Static detectors use stdlib only.  LLM-based custom rules are loaded
from ``.vc-context/conventions.json`` (``anti_patterns`` key) and
require a running Ollama instance configured via ``chat_provider``.
"""

from __future__ import annotations

import ast
import glob as _glob
import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

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


def has_static_rule(name: str) -> bool:
    return name in _DETECTORS


def find_anti_patterns(project_root: str, rule: str) -> List[Dict[str, Any]]:
    """Run one registered detector. Returns ``[]`` for unknown rule
    names (caller can compare against :func:`list_anti_patterns`).
    """
    detector = _DETECTORS.get(rule)
    if detector is None:
        return []
    return detector(project_root)


# ----------------------------------------------------------------------
# LLM-based custom rules
# ----------------------------------------------------------------------


def load_llm_rules(project_root: str) -> List[Dict[str, Any]]:
    """Return custom LLM-based rules from ``.vc-context/conventions.json``.

    Expected shape::

        "anti_patterns": [
          {"name": "raw-sql-in-view",
           "description": "Direct SQL queries inside view functions",
           "scope": "web_services/**/*.py"}
        ]

    ``scope`` is an optional glob relative to ``project_root``; default
    ``**/*.py`` (all Python files, excluding ``_IGNORE_DIRS``).
    """
    conv = os.path.join(project_root, ".vc-context", "conventions.json")
    if not os.path.isfile(conv):
        return []
    try:
        with open(conv, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    return [
        r
        for r in cfg.get("anti_patterns", [])
        if isinstance(r, dict) and r.get("name") and r.get("description")
    ]


def _files_for_scope(project_root: str, scope: str) -> List[str]:
    """Return absolute paths of Python files matching ``scope`` glob,
    skipping ``_IGNORE_DIRS``.
    """
    pattern = os.path.join(project_root, scope.replace("/", os.sep))
    matched = _glob.glob(pattern, recursive=True)
    out = []
    for f in matched:
        if not f.endswith(".py"):
            continue
        rel_parts = os.path.relpath(f, project_root).replace(os.sep, "/").split("/")
        if not any(p in _IGNORE_DIRS for p in rel_parts):
            out.append(f)
    return sorted(out)


def _extract_chunks(source: str, tree: ast.AST) -> List[Tuple[str, str, int]]:
    """Return ``(qualified_name, source_text, start_line)`` for every
    top-level function and class method.  Source text is capped at 100
    lines to keep LLM prompts bounded.
    """
    lines = source.splitlines()
    chunks: List[Tuple[str, str, int]] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _add_chunk(chunks, lines, node.name, node)
        elif isinstance(node, ast.ClassDef):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _add_chunk(chunks, lines, f"{node.name}.{child.name}", child)

    return chunks


def _add_chunk(
    chunks: List[Tuple[str, str, int]],
    lines: List[str],
    name: str,
    node: ast.AST,
) -> None:
    start = node.lineno - 1  # type: ignore[attr-defined]
    end = getattr(node, "end_lineno", start + 1)
    chunk_lines = lines[start:end]
    if len(chunk_lines) > 100:
        chunk_lines = chunk_lines[:80] + ["    # ... (truncated)"] + chunk_lines[-10:]
    chunks.append((name, "\n".join(chunk_lines), node.lineno))  # type: ignore[attr-defined]


def detect_with_llm(
    project_root: str,
    rule_def: Dict[str, Any],
    chat: Any,
    cache: Dict[Tuple, List[Dict[str, Any]]],
    *,
    max_chunks_per_file: int = 20,
) -> List[Dict[str, Any]]:
    """Run LLM-based detection for a custom rule definition.

    ``cache`` is a session-level dict provided by the caller (QueryEngine
    ClassVar) — keyed by ``(rule_name, abs_path, mtime_int)``.  Unchanged
    files are not re-scanned within a session.
    """
    rule_name: str = rule_def["name"]
    description: str = rule_def["description"]
    scope: str = rule_def.get("scope", "**/*.py")

    out: List[Dict[str, Any]] = []

    for full in _files_for_scope(project_root, scope):
        rel = os.path.relpath(full, project_root).replace(os.sep, "/")
        try:
            mtime = int(os.path.getmtime(full))
        except OSError:
            continue
        cache_key: Tuple = (rule_name, full, mtime)
        if cache_key in cache:
            out.extend(cache[cache_key])
            continue

        try:
            with open(full, encoding="utf-8") as fh:
                source = fh.read()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue

        chunks = _extract_chunks(source, tree)[:max_chunks_per_file]
        hits: List[Dict[str, Any]] = []

        for func_name, chunk_source, start_line in chunks:
            prompt = (
                f'Anti-pattern: "{description}"\n\n'
                f"Python code:\n```python\n{chunk_source}\n```\n\n"
                "Does this code contain the above anti-pattern?\n"
                "Reply with exactly one of:\n"
                "YES: <one-line evidence describing the specific violation>\n"
                "NO"
            )
            try:
                resp = chat.generate(prompt, timeout=20).strip()
            except Exception:
                continue

            if resp.upper().startswith("YES"):
                evidence = resp[3:].lstrip(":").strip() or description
                hits.append(
                    {
                        "rule": rule_name,
                        "file": rel,
                        "line": start_line,
                        "function": func_name,
                        "evidence": evidence,
                    }
                )

        cache[cache_key] = hits
        out.extend(hits)

    out.sort(key=lambda r: (r["file"], r["line"]))
    return out
