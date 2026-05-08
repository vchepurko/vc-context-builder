"""Angular route bridge — parse ``RouterModule.forRoot`` / ``forChild`` /
``provideRouter`` route arrays and emit a path→component map.

Output artifact ``agent_ng_routes.json`` has one entry per route::

    [
        {
            "path":     "users/:id",
            "component": "UserDetailComponent",  // null when lazy/redirect
            "file":     "src/app/app-routing.module.ts",
            "line":     42,
            "lazy":     false,
            "guards":   ["AuthGuard"],
            "children": null  // or list of nested routes (1 level)
        },
        ...
    ]

The bridge sits next to ``route_bridge.py`` (HTTP routes for Express /
FastAPI) — parallel concept, different schema. Keeps Angular reasoning
(``which component handles /users/42 ?``) one MCP call away without
overloading the HTTP-flavoured route artifact.

Heuristic regex parser. Computed paths, dynamic component refs, and
deeply-nested children fall through silently — confirm with grep for
those edge cases. The parser deliberately covers the common 80% so
indexing stays fast and stdlib-only.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

NG_ROUTES_FILENAME = "agent_ng_routes.json"

_IGNORE_DIRS = frozenset(
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

# Route-array openers we look for. We don't try to balance braces in
# the regex itself — instead we scan for one of these markers, then
# walk forward with a brace counter to find the array's close. That
# keeps the parser tolerant of arbitrary content (children:[...],
# template strings, comments) inside the array.
_RE_ROUTE_ARRAY_OPENER = re.compile(
    r"\b(?:"
    r"RouterModule\s*\.\s*for(?:Root|Child)\s*\(\s*\["
    r"|"
    r"provideRouter\s*\(\s*\["
    r"|"
    r"const\s+\w+\s*:\s*Routes\s*=\s*\["
    r")"
)

# Per-route-object field extractors. Run on each balanced `{...}` slice
# that the array walker yields.
_RE_PATH = re.compile(r"path\s*:\s*['\"`]([^'\"`]*)['\"`]")
_RE_COMPONENT = re.compile(r"component\s*:\s*([A-Za-z_$][A-Za-z0-9_$]*)")
_RE_LOAD_CHILDREN = re.compile(r"loadChildren\s*:")
_RE_REDIRECT = re.compile(r"redirectTo\s*:\s*['\"`]([^'\"`]*)['\"`]")
_RE_GUARDS = re.compile(r"can(?:Activate|ActivateChild|Deactivate|Load|Match)\s*:\s*\[([^\]]+)\]")
_RE_GUARD_NAME = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\b")


def _walk_ts_files(project_root: str) -> list[str]:
    """All ``*.ts`` files under the project, skipping vendored / build dirs.

    Specifically NOT skipping ``.spec.ts`` files — a project might
    declare test-only routes there. Rare in practice; fast either way.
    """
    if not os.path.isdir(project_root):
        return []
    out: list[str] = []
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]
        for fname in files:
            if fname.endswith((".ts", ".tsx")):
                out.append(os.path.join(cur, fname))
    return out


def _balance_array(text: str, open_idx: int) -> int:
    """Given ``text[open_idx] == '['``, return index of matching ``]``.

    Returns -1 when unbalanced (truncated source / parse failure). The
    walker treats unbalanced arrays as no-match so we never emit
    half-formed records.
    """
    depth = 0
    in_string: Optional[str] = None
    i = open_idx
    while i < len(text):
        ch = text[i]
        if in_string is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_string = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top_level_objects(array_body: str) -> list[tuple[str, int]]:
    """Yield ``[(object_text, offset_within_body), ...]`` for each
    top-level ``{...}`` element of an array.

    Skips brace-balancing inside strings/templates/comments — those
    aren't perfectly parsed but the regex captures (path / component
    / guards) treat them later by running on the slice.
    """
    out: list[tuple[str, int]] = []
    depth = 0
    in_string: Optional[str] = None
    obj_start: Optional[int] = None
    i = 0
    while i < len(array_body):
        ch = array_body[i]
        if in_string is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_string = ch
        elif ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                out.append((array_body[obj_start : i + 1], obj_start))
                obj_start = None
        i += 1
    return out


def _extract_route_record(
    obj_text: str,
    *,
    file_rel: str,
    line: int,
) -> Optional[dict[str, Any]]:
    """Build one route record from an object literal slice.

    Returns ``None`` when the slice has no ``path`` field — those are
    config objects (data, resolve, etc.) that crept into the array
    walker's view. Computed components / unknown shapes are kept with
    ``component=None`` so downstream still surfaces the path.
    """
    path_m = _RE_PATH.search(obj_text)
    if path_m is None:
        return None
    path_value = path_m.group(1)

    component = None
    comp_m = _RE_COMPONENT.search(obj_text)
    if comp_m is not None:
        component = comp_m.group(1)

    lazy = bool(_RE_LOAD_CHILDREN.search(obj_text))
    redirect_m = _RE_REDIRECT.search(obj_text)
    redirect_to = redirect_m.group(1) if redirect_m is not None else None

    guards: list[str] = []
    for g_m in _RE_GUARDS.finditer(obj_text):
        for n_m in _RE_GUARD_NAME.finditer(g_m.group(1)):
            name = n_m.group(1)
            if name and name not in guards:
                guards.append(name)

    return {
        "path": path_value,
        "component": component,
        "file": file_rel,
        "line": line,
        "lazy": lazy,
        "redirect_to": redirect_to,
        "guards": guards,
    }


def _line_of(content: str, offset: int) -> int:
    """1-indexed line number for a character offset."""
    return content.count("\n", 0, offset) + 1


def _routes_in_file(file_path: str, project_root: str) -> list[dict[str, Any]]:
    """Scan one TS file and return every route record found.

    Misses computed paths, dynamic spreads, and deeply-nested children
    (we walk one level — the array passed to ``forRoot`` is parsed
    top-level only). Children blocks aren't recursively expanded;
    instead they show up as part of the parent slice.
    """
    try:
        with open(file_path, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return []
    if (
        "RouterModule" not in content
        and "provideRouter" not in content
        and ": Routes" not in content
    ):
        # Fast path: skip the regex+walker work on files that clearly
        # don't host routing config.
        return []

    rel = os.path.relpath(file_path, project_root).replace(os.sep, "/")
    out: list[dict[str, Any]] = []
    for opener in _RE_ROUTE_ARRAY_OPENER.finditer(content):
        # `[` is the last char of the opener regex.
        open_idx = opener.end() - 1
        close_idx = _balance_array(content, open_idx)
        if close_idx == -1:
            continue
        body = content[open_idx + 1 : close_idx]
        for obj_text, obj_offset in _split_top_level_objects(body):
            line = _line_of(content, open_idx + 1 + obj_offset)
            rec = _extract_route_record(obj_text, file_rel=rel, line=line)
            if rec is not None:
                out.append(rec)
    return out


def build_ng_route_index(project_root: str) -> list[dict[str, Any]]:
    """Project-wide sweep. Returns the ordered list of route records.

    Order: by ``(file, line)`` so the artifact diff is stable across
    rebuilds.
    """
    out: list[dict[str, Any]] = []
    for ts_path in _walk_ts_files(project_root):
        out.extend(_routes_in_file(ts_path, project_root))
    out.sort(key=lambda r: (r["file"], r["line"]))
    return out


def write_ng_route_index(project_root: str, routes: list[dict[str, Any]]) -> str:
    """Persist ``agent_ng_routes.json`` and return its absolute path."""
    out_path = os.path.join(project_root, NG_ROUTES_FILENAME)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(routes, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return out_path


# ----------------------------------------------------------------------
# Read-side queries (used by query_engine + MCP).
# ----------------------------------------------------------------------


def routes_for_component(
    routes: list[dict[str, Any]],
    name: str,
) -> list[dict[str, Any]]:
    """All routes whose ``component`` field equals *name* (exact)."""
    if not name:
        return []
    return [r for r in routes if r.get("component") == name]


def route_for_path(
    routes: list[dict[str, Any]],
    path: str,
) -> list[dict[str, Any]]:
    """All routes whose ``path`` matches *path* (exact, including '').

    Two passes — exact first, then a substring contains so a query for
    ``users`` finds ``users/:id`` and ``admin/users``. Caller can
    inspect the order to tell the two apart.
    """
    if path is None:
        return []
    exact = [r for r in routes if r.get("path") == path]
    if exact:
        return exact
    needle = path.strip("/")
    if not needle:
        return []
    return [r for r in routes if needle in (r.get("path") or "")]
