"""Convention linter — turn declared project rules into enforced policy.

The user declares rules in ``.vc-context/conventions.json`` at the
parent project root; this module loads them, walks the project tree,
and reports violations.

Stdlib only. Two rule kinds for now (extensible):

- ``forbid_import: <package>`` — fail if a Python file imports
  ``<package>`` (either ``import <package>...`` or
  ``from <package>... import ...``).
- ``forbid_call: <symbol>`` — fail if a Python file calls
  ``<symbol>(...)`` anywhere (top-level or nested).

Each rule carries:

- ``id`` — short stable identifier (used in CLI/MCP output).
- ``description`` — human-readable explanation.
- ``match_path`` — ``fnmatch``-style glob; rule only applies to files
  whose project-relative path matches.
- ``severity`` — one of ``error`` / ``warn`` / ``info``. Only ``error``
  flips the CLI exit code.

Missing config = empty list. Not an error.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional


CONFIG_RELATIVE_PATH = os.path.join(".vc-context", "conventions.json")

VALID_SEVERITIES = ("error", "warn", "info")
VALID_RULE_KEYS = ("forbid_import", "forbid_call", "forbid_decorator_regex")

# Project subtrees we never walk.
IGNORE_DIRS = {
    ".git", "node_modules", "vendor", "__pycache__",
    "dist", "build", ".venv", "venv", ".idea", ".vscode",
    ".ai-context", ".vc-context",
}


# ----------------------------------------------------------------------
# Config loading
# ----------------------------------------------------------------------

def load_rules(project_root: str) -> List[Dict[str, Any]]:
    """Return the rule list from ``.vc-context/conventions.json``.

    Missing file → empty list. Malformed JSON or unexpected shape →
    empty list (we never raise from a query path; the linter must
    degrade gracefully).
    """
    config_path = os.path.join(project_root, CONFIG_RELATIVE_PATH)
    if not os.path.isfile(config_path):
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        return []
    cleaned: List[Dict[str, Any]] = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        rule_id = r.get("id")
        match_path = r.get("match_path") or "**/*.py"
        severity = r.get("severity") or "warn"
        if severity not in VALID_SEVERITIES:
            severity = "warn"
        # At least one supported rule key must be present.
        if not any(k in r for k in VALID_RULE_KEYS):
            continue
        if not isinstance(rule_id, str) or not rule_id:
            continue
        # Pre-compile the decorator regex once, surface the literal
        # source for diagnostic messages. Bad regex → silently drop
        # the rule (keeps the linter degrade-gracefully contract).
        forbid_decorator_regex_raw = r.get("forbid_decorator_regex")
        forbid_decorator_pattern = None
        if isinstance(forbid_decorator_regex_raw, str) and forbid_decorator_regex_raw:
            try:
                forbid_decorator_pattern = re.compile(forbid_decorator_regex_raw)
            except re.error:
                forbid_decorator_pattern = None
                # No raw — drop this rule entirely if we couldn't compile
                # AND the rule has no other rule keys.
                if not any(r.get(k) for k in ("forbid_import", "forbid_call")):
                    continue

        cleaned.append({
            "id": rule_id,
            "description": r.get("description") or "",
            "match_path": match_path,
            "severity": severity,
            "forbid_import": r.get("forbid_import"),
            "forbid_call": r.get("forbid_call"),
            "forbid_decorator_regex": forbid_decorator_regex_raw,
            "_decorator_pattern": forbid_decorator_pattern,
        })
    return cleaned


# ----------------------------------------------------------------------
# Glob matching
# ----------------------------------------------------------------------

def _glob_match(rel_path: str, pattern: str) -> bool:
    """``fnmatch`` extended to support ``**`` segments.

    Semantics (matching common globstar conventions):

    - ``**`` matches **zero or more** path segments. So
      ``bot/handlers/**/*.py`` matches both ``bot/handlers/x.py`` and
      ``bot/handlers/sub/x.py``.
    - ``*`` matches anything inside a single segment.
    - All other ``fnmatch`` metacharacters work as normal.
    """
    # Normalise separators.
    rel = rel_path.replace(os.sep, "/")
    pat = pattern.replace(os.sep, "/")

    if "**" not in pat:
        return fnmatch.fnmatch(rel, pat)

    # Handle ``**`` by expanding to two alternatives: one where ``**``
    # matches zero segments (collapsing the surrounding slashes) and
    # one where it matches one or more segments. We do this for the
    # leftmost ``**`` and recurse so nested cases work too.
    idx = pat.find("**")
    before = pat[:idx]
    after = pat[idx + 2:]

    # Variant 1: ``**`` is zero segments. Adjacent slashes collapse —
    # ``a/**/b`` becomes ``a/b``, ``**/b`` becomes ``b``.
    if before.endswith("/") and after.startswith("/"):
        zero = before[:-1] + after  # drop one of the slashes
    elif before.endswith("/") and after == "":
        zero = before[:-1]  # ``a/**`` → ``a``
    elif before == "" and after.startswith("/"):
        zero = after[1:]  # ``**/b`` → ``b``
    else:
        zero = before + after

    # Variant 2: ``**`` matches one or more segments — translate to a
    # plain ``*`` (fnmatch's ``*`` matches across slashes since we
    # don't anchor segments individually).
    one_or_more = before + "*" + after

    if "**" in zero:
        if _glob_match(rel, zero):
            return True
    elif fnmatch.fnmatch(rel, zero):
        return True

    if "**" in one_or_more:
        return _glob_match(rel, one_or_more)
    return fnmatch.fnmatch(rel, one_or_more)


# ----------------------------------------------------------------------
# Per-file scanning
# ----------------------------------------------------------------------

def _iter_python_files(project_root: str) -> Iterable[str]:
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(cur, f)


def _rel(path: str, project_root: str) -> str:
    try:
        rel = os.path.relpath(path, project_root)
    except ValueError:
        rel = path
    return rel.replace(os.sep, "/")


def _scan_file(file_path: str, source: str, applicable: List[Dict[str, Any]],
               rel_path: str) -> List[Dict[str, Any]]:
    """Return violation records for one file.

    ``applicable`` is the subset of rules whose ``match_path`` already
    matched ``rel_path`` — caller does the filter once.
    """
    if not applicable:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    forbid_imports = [r for r in applicable if r.get("forbid_import")]
    forbid_calls = [r for r in applicable if r.get("forbid_call")]
    forbid_decorators = [r for r in applicable if r.get("_decorator_pattern")]

    out: List[Dict[str, Any]] = []

    if forbid_decorators:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for dec in getattr(node, "decorator_list", []) or ():
                try:
                    dec_text = "@" + ast.unparse(dec)
                except Exception:
                    continue
                for rule in forbid_decorators:
                    pat = rule["_decorator_pattern"]
                    if pat.search(dec_text):
                        out.append(_record(
                            rule, rel_path, getattr(dec, "lineno", node.lineno),
                            f"forbidden decorator pattern: {dec_text}",
                        ))

    if forbid_imports:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    head = alias.name.split(".", 1)[0]
                    for rule in forbid_imports:
                        target = rule["forbid_import"]
                        if head == target or alias.name == target or alias.name.startswith(target + "."):
                            out.append(_record(rule, rel_path, node.lineno,
                                               f"forbidden import: {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                head = mod.split(".", 1)[0] if mod else ""
                for rule in forbid_imports:
                    target = rule["forbid_import"]
                    if mod and (head == target or mod == target or mod.startswith(target + ".")):
                        out.append(_record(rule, rel_path, node.lineno,
                                           f"forbidden import: from {mod}"))

    if forbid_calls:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = _call_name(node.func)
            if called is None:
                continue
            for rule in forbid_calls:
                target = rule["forbid_call"]
                if called == target:
                    out.append(_record(rule, rel_path, node.lineno,
                                       f"forbidden call: {target}(...)"))

    return out


def _call_name(func: ast.AST) -> Optional[str]:
    """Extract the call's leaf name, e.g. ``print`` or ``logger.info`` → ``info``.

    For the linter we want to match the leaf name (so
    ``foo.print(...)`` does NOT trigger ``forbid_call: print``, but a
    bare ``print(...)`` does). Matches are exact on the leaf.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        # We only flag bare-name calls; an attribute access (e.g.
        # ``logger.print(...)``) is intentionally not matched.
        return None
    return None


def _record(rule: Dict[str, Any], file_path: str, line: int, message: str) -> Dict[str, Any]:
    return {
        "rule_id": rule["id"],
        "file": file_path,
        "line": line,
        "severity": rule["severity"],
        "message": message,
    }


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def lint_project(project_root: str) -> List[Dict[str, Any]]:
    """Run all rules across every Python file under ``project_root``.

    Returns a list of violation records, ordered by file then line.
    Empty list when the config is missing or no rules trigger.
    """
    rules = load_rules(project_root)
    if not rules:
        return []

    violations: List[Dict[str, Any]] = []
    for full in _iter_python_files(project_root):
        rel = _rel(full, project_root)
        applicable = [r for r in rules if _glob_match(rel, r["match_path"])]
        if not applicable:
            continue
        try:
            with open(full, "r", encoding="utf-8") as fh:
                source = fh.read()
        except OSError:
            continue
        violations.extend(_scan_file(full, source, applicable, rel))

    violations.sort(key=lambda v: (v["file"], v["line"], v["rule_id"]))
    return violations


def has_error(violations: List[Dict[str, Any]]) -> bool:
    """True if any violation is severity ``error`` (drives CLI exit code)."""
    return any(v.get("severity") == "error" for v in violations)
