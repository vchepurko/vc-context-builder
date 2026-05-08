"""Configurable role detection — let any project declare custom roles.

Today the built-in role vocabulary is Python-specific (route, webhook,
migration, scheduler-job, repository, service, api-client) plus a small
set of JS/TS roles wired into ``ts_js_parser``. That's enough for the
projects this submodule was originally written against, but useless on
a Go/Ruby/Express/WordPress codebase.

This module reads ``.vc-context/roles.json`` at the parent project root
and applies each declared rule to every export the parser already
produced. A rule is a dict of glob-and-regex matchers; it tags a
matching export with ``role: <id>``. Multiple rules can apply to the
same symbol — the highest-priority one wins.

Schema (validated leniently — unknown keys are ignored, malformed rules
are skipped, the whole config is optional):

    {
      "roles": [
        {
          "id": "express-route",
          "match_path": "**/*.{js,ts}",
          "match_decorator_or_call": "(app|router)\\.(get|post|...)",
          "match_function_name": "...",
          "match_function_returns": "...",
          "match_call": "...",
          "match_kind": "func|async-func|class",
          "priority": 10
        }
      ]
    }

Stdlib only by design.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CONFIG_RELATIVE_PATH = os.path.join(".vc-context", "roles.json")

# Built-in roles default to priority 0; custom roles default to 5 if the
# user didn't declare one, so they always override built-ins by default.
DEFAULT_CUSTOM_PRIORITY = 5
BUILTIN_PRIORITY = 0

VALID_KINDS = {"func", "async-func", "class"}


@dataclass
class CustomRole:
    """One declared role — all matchers are optional, but at least one
    must be set or the rule is silently dropped at load time.
    """

    id: str
    priority: int = DEFAULT_CUSTOM_PRIORITY
    match_path: Optional[str] = None
    match_decorator_or_call: Optional[re.Pattern] = None
    match_function_name: Optional[re.Pattern] = None
    match_function_returns: Optional[re.Pattern] = None
    match_call: Optional[re.Pattern] = None
    match_kind: Optional[str] = None
    # Raw source patterns — kept around purely for debugging / tests.
    raw: Dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Config loading
# ----------------------------------------------------------------------


def load_custom_roles(project_root: str) -> List[CustomRole]:
    """Return the list of declared custom roles.

    Missing config / malformed JSON / unexpected shape → empty list.
    Never raises — query paths must degrade gracefully.
    """
    config_path = os.path.join(project_root, CONFIG_RELATIVE_PATH)
    if not os.path.isfile(config_path):
        return []
    try:
        with open(config_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    raw_rules = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(raw_rules, list):
        return []

    out: List[CustomRole] = []
    for r in raw_rules:
        rule = _parse_rule(r)
        if rule is not None:
            out.append(rule)
    # Stable order: highest priority first so first-match wins on ties.
    out.sort(key=lambda x: (-x.priority, x.id))
    return out


def _parse_rule(raw: Any) -> Optional[CustomRole]:
    if not isinstance(raw, dict):
        return None
    rid = raw.get("id")
    if not isinstance(rid, str) or not rid.strip():
        return None

    priority = raw.get("priority", DEFAULT_CUSTOM_PRIORITY)
    if not isinstance(priority, int):
        priority = DEFAULT_CUSTOM_PRIORITY

    match_path = raw.get("match_path")
    if not isinstance(match_path, str) or not match_path:
        match_path = None

    match_kind = raw.get("match_kind")
    if isinstance(match_kind, str) and match_kind in VALID_KINDS:
        match_kind = match_kind
    else:
        match_kind = None

    decorator = _compile(raw.get("match_decorator_or_call"))
    fname = _compile(raw.get("match_function_name"))
    freturns = _compile(raw.get("match_function_returns"))
    fcall = _compile(raw.get("match_call"))

    # At least one matcher must be present — otherwise the rule would
    # tag every export in the project, which is almost certainly not
    # what the user wanted.
    if not any([match_path, match_kind, decorator, fname, freturns, fcall]):
        return None

    return CustomRole(
        id=rid.strip(),
        priority=priority,
        match_path=match_path,
        match_decorator_or_call=decorator,
        match_function_name=fname,
        match_function_returns=freturns,
        match_call=fcall,
        match_kind=match_kind,
        raw=raw,
    )


def _compile(pattern: Any) -> Optional[re.Pattern]:
    if not isinstance(pattern, str) or not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error:
        return None


# ----------------------------------------------------------------------
# Glob helper — supports ``**`` and ``{a,b}`` brace alternation
# ----------------------------------------------------------------------


def _expand_braces(pattern: str) -> List[str]:
    """Expand ``{a,b,c}`` alternations into a list of fnmatch globs.

    Only handles a single, non-nested brace group — that covers the
    common ``**/*.{js,ts}`` case and keeps the implementation small.
    """
    if "{" not in pattern or "}" not in pattern:
        return [pattern]
    start = pattern.find("{")
    end = pattern.find("}", start + 1)
    if end == -1:
        return [pattern]
    head = pattern[:start]
    tail = pattern[end + 1 :]
    body = pattern[start + 1 : end]
    parts = [p for p in body.split(",") if p]
    if not parts:
        return [pattern]
    out: List[str] = []
    for p in parts:
        for sub in _expand_braces(head + p + tail):
            out.append(sub)
    return out


def _glob_match(rel_path: str, pattern: str) -> bool:
    """Glob with ``**`` (any number of segments) and ``{a,b}`` support.

    Mirrors the convention used by ``conventions.py`` for consistency.
    """
    rel = rel_path.replace(os.sep, "/")
    for variant in _expand_braces(pattern):
        if _glob_one(rel, variant):
            return True
    return False


def _glob_one(rel: str, pat: str) -> bool:
    pat = pat.replace(os.sep, "/")
    if "**" not in pat:
        return fnmatch.fnmatch(rel, pat)

    idx = pat.find("**")
    before = pat[:idx]
    after = pat[idx + 2 :]

    if before.endswith("/") and after.startswith("/"):
        zero = before[:-1] + after
    elif before.endswith("/") and after == "":
        zero = before[:-1]
    elif before == "" and after.startswith("/"):
        zero = after[1:]
    else:
        zero = before + after

    one_or_more = before + "*" + after

    if "**" in zero:
        if _glob_one(rel, zero):
            return True
    elif fnmatch.fnmatch(rel, zero):
        return True

    if "**" in one_or_more:
        return _glob_one(rel, one_or_more)
    return fnmatch.fnmatch(rel, one_or_more)


# ----------------------------------------------------------------------
# Per-export application
# ----------------------------------------------------------------------


def apply_custom_roles(
    export: Dict[str, Any],
    file_path: str,
    source_text: str,
    custom_roles: List[CustomRole],
    project_root: Optional[str] = None,
) -> Optional[str]:
    """Try every rule against one export. Return the highest-priority
    match's id, or ``None`` when nothing fires.

    ``file_path`` may be absolute or relative; the path matcher is
    applied against the project-relative form when ``project_root`` is
    supplied, otherwise against the path as given (forward-slashed).
    """
    if not custom_roles:
        return None

    rel_path = _project_rel(file_path, project_root)
    body = _function_body_text(export, source_text)
    decorators_text = export.get("_decorators_text") or ""

    best: Optional[CustomRole] = None
    for rule in custom_roles:
        if not _rule_matches(rule, export, rel_path, body, decorators_text):
            continue
        if best is None or rule.priority > best.priority:
            best = rule

    return best.id if best else None


def _rule_matches(
    rule: CustomRole,
    export: Dict[str, Any],
    rel_path: str,
    body: str,
    decorators_text: str,
) -> bool:
    if rule.match_path is not None and not _glob_match(rel_path, rule.match_path):
        return False
    if rule.match_kind is not None and export.get("kind") != rule.match_kind:
        return False
    if rule.match_function_name is not None:
        name = export.get("name") or ""
        if not rule.match_function_name.search(name):
            return False
    if rule.match_decorator_or_call is not None:
        # Match against decorators (Python) and the registration call
        # text the JS/TS parser supplies under ``_register_call``.
        register_call = export.get("_register_call") or ""
        haystack = decorators_text + "\n" + register_call
        if not rule.match_decorator_or_call.search(haystack):
            return False
    if rule.match_function_returns is not None:
        if not rule.match_function_returns.search(body):
            return False
    if rule.match_call is not None:
        if not rule.match_call.search(body):
            return False
    return True


def _function_body_text(export: Dict[str, Any], source_text: str) -> str:
    """Return the function body slice for regex matching.

    The parser may stash the body under ``_body`` (rich case); else we
    fall back to using the entire source — over-broad but harmless for
    a regex match that's already constrained by name / kind / path.
    """
    body = export.get("_body")
    if isinstance(body, str) and body:
        return body
    return source_text or ""


def _project_rel(file_path: str, project_root: Optional[str]) -> str:
    if not project_root:
        return file_path.replace(os.sep, "/")
    try:
        rel = os.path.relpath(file_path, project_root)
    except ValueError:
        rel = file_path
    rel = rel.replace(os.sep, "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


# ----------------------------------------------------------------------
# Built-in priority comparator
# ----------------------------------------------------------------------


def should_override_builtin(
    custom_role_id: Optional[str], custom_priority: int, builtin_role: Optional[str]
) -> bool:
    """Decide whether the custom role replaces the built-in tag.

    Built-in roles always have priority 0. Custom roles win iff their
    priority is strictly greater than 0 — i.e. the user opted into the
    override by setting (or accepting the default) priority of 5.
    """
    if not custom_role_id:
        return False
    if not builtin_role:
        return True
    return custom_priority > BUILTIN_PRIORITY
