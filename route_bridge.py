"""Cross-language route bridge — link FastAPI/aiogram routes to JS callers.

For every Python ``route`` decorator we extract the URL path
(e.g. ``@router.get("/api/foo")`` → ``/api/foo``). Then we walk every
``.js`` / ``.ts`` / ``.jsx`` / ``.tsx`` source under the project,
pick out ``fetch(...)``, ``.get(...)``, ``.post(...)``, etc. call
sites, strip ``${…}`` template parts to get a path-prefix, and match
prefix-style against the route table.

Output: ``agent_routes.json`` ::

    {
      "/api/foo": {
        "method": "GET",
        "handler": "find_foo",
        "file": "backend/routes/x.py",
        "line": 42,
        "callers_js": [
          {"file": "webapp/static/lib/api.js", "line": 17,
           "raw": "/api/foo"}
        ]
      },
      ...
    }

All zero-dep. Regex-only on the JS side; we don't try to parse
TypeScript ASTs from stdlib.
"""

from __future__ import annotations

import ast
import json
import os
import re
from collections.abc import Iterable
from typing import Any, Dict, List, Optional, Tuple

ROUTES_FILENAME = "agent_routes.json"

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

# Decorator method names that mark a FastAPI HTTP route. (Mirrors
# ``symbols.py`` — kept local to dodge a circular import risk.)
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head", "trace", "api_route"}


# ----------------------------------------------------------------------
# Python side: pull route paths from decorators
# ----------------------------------------------------------------------


def _iter_python_files(project_root: str) -> Iterable[str]:
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(cur, f)


def _decorator_method(dec: ast.AST) -> Optional[str]:
    func = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _extract_path_arg(call: ast.Call) -> Optional[str]:
    """First positional string arg, or ``path=...`` kwarg."""
    if call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    for kw in call.keywords or ():
        if kw.arg in {"path", "endpoint"} and isinstance(kw.value, ast.Constant):
            v = kw.value.value
            if isinstance(v, str):
                return v
    return None


def collect_python_routes(project_root: str) -> List[Dict[str, Any]]:
    """Return every route decorator we can detect.

    Each record: ``{path, method, handler, file, line}``. ``method``
    is upper-cased (``GET`` / ``POST`` / ...).
    """
    out: List[Dict[str, Any]] = []
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
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list or ():
                method = _decorator_method(dec)
                if method not in HTTP_METHODS:
                    continue
                if not isinstance(dec, ast.Call):
                    continue
                url_path = _extract_path_arg(dec)
                if not url_path or not url_path.startswith("/"):
                    continue
                rel = os.path.relpath(full, project_root).replace(os.sep, "/")
                out.append(
                    {
                        "path": url_path,
                        "method": method.upper(),
                        "handler": node.name,
                        "file": rel,
                        "line": node.lineno,
                    }
                )
    # Stable order so artifacts diff cleanly.
    out.sort(key=lambda r: (r["path"], r["method"], r["file"]))
    return out


# ----------------------------------------------------------------------
# JS/TS side: pull URL strings from fetch / .get / .post / etc.
# ----------------------------------------------------------------------

# We match three call shapes:
#
#   fetch('<URL>', ...)               # native + libraries
#   fetch(`<URL>`)                    # template literal
#   <ident>.get('<URL>', ...)         # axios / wrapper / instance
#   <ident>.post(...)                 # ditto for other verbs
#
# The path argument is the FIRST argument; we don't track kwargs.
_FETCH_RE = re.compile(
    r"""\bfetch\s*\(\s*(?P<q>['"`])(?P<url>[^'"`]+)(?P=q)""",
    re.MULTILINE,
)
_VERB_RE = re.compile(
    r"""\b\.\s*(?P<verb>get|post|put|delete|patch|head|options)"""
    r"""\s*\(\s*(?P<q>['"`])(?P<url>[^'"`]+)(?P=q)""",
    re.MULTILINE | re.IGNORECASE,
)

JS_EXTENSIONS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}


def _iter_js_files(project_root: str) -> Iterable[str]:
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if os.path.splitext(f)[1] in JS_EXTENSIONS:
                yield os.path.join(cur, f)


def _strip_templates(url: str) -> str:
    """Replace ``${…}`` (and bare ``:param`` segments) with ``*``.

    Keeps the path-prefix structure: ``/api/products/${id}`` →
    ``/api/products/*``. That makes it easy to compare against routes
    that contain ``{id}`` placeholders.
    """
    cleaned = re.sub(r"\$\{[^}]*\}", "*", url)
    cleaned = re.sub(r":[A-Za-z_][A-Za-z0-9_]*", "*", cleaned)
    # Drop trailing query strings — they aren't part of the route.
    if "?" in cleaned:
        cleaned = cleaned.split("?", 1)[0]
    return cleaned


def _line_for(source: str, offset: int) -> int:
    """1-based line number for a regex match offset."""
    return source.count("\n", 0, offset) + 1


def collect_js_calls(project_root: str) -> List[Dict[str, Any]]:
    """Return every JS/TS call site that looks like an HTTP request.

    Each record: ``{file, line, raw, prefix, verb}``. ``prefix`` is
    the template-stripped URL (``/api/products/*``); ``verb`` is the
    lowercase HTTP verb when we picked it from a ``.get/.post/...``
    call, otherwise ``"fetch"``.
    """
    out: List[Dict[str, Any]] = []
    for full in _iter_js_files(project_root):
        try:
            with open(full, encoding="utf-8") as fh:
                source = fh.read()
        except OSError:
            continue
        rel = os.path.relpath(full, project_root).replace(os.sep, "/")

        for m in _FETCH_RE.finditer(source):
            url = m.group("url")
            if not url.startswith("/"):
                continue  # absolute URLs don't belong to our backend
            out.append(
                {
                    "file": rel,
                    "line": _line_for(source, m.start()),
                    "raw": url,
                    "prefix": _strip_templates(url),
                    "verb": "fetch",
                }
            )
        for m in _VERB_RE.finditer(source):
            url = m.group("url")
            if not url.startswith("/"):
                continue
            out.append(
                {
                    "file": rel,
                    "line": _line_for(source, m.start()),
                    "raw": url,
                    "prefix": _strip_templates(url),
                    "verb": m.group("verb").lower(),
                }
            )
    out.sort(key=lambda r: (r["file"], r["line"]))
    return out


# ----------------------------------------------------------------------
# Match calls to routes
# ----------------------------------------------------------------------

_PARAM_RE = re.compile(r"\{[^}]+\}")


def _route_to_pattern(route_path: str) -> str:
    """Turn ``/foo/{id}/bar`` into a regex string matching the JS prefix.

    ``{id}`` becomes ``[^/]+`` and ``*`` (from JS template stripping)
    becomes ``[^/]+`` too.
    """
    escaped = re.escape(route_path)
    # un-escape ``\{`` etc — we want to substitute placeholders.
    escaped = escaped.replace(r"\{", "{").replace(r"\}", "}")
    pattern = _PARAM_RE.sub(r"[^/]+", escaped)
    return "^" + pattern + "$"


def _normalise_for_match(prefix: str) -> str:
    """Replace JS ``*`` with placeholder, drop trailing slashes."""
    p = prefix
    if p.endswith("/") and len(p) > 1:
        p = p.rstrip("/")
    return p


def match_calls_to_routes(
    routes: List[Dict[str, Any]],
    js_calls: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Build the ``agent_routes.json`` payload.

    Strategy: for each JS call, scan the route list and pick the route
    whose pattern matches AND whose verb agrees (when the call has a
    verb — bare ``fetch`` matches any verb).
    """
    # Pre-compile route patterns once.
    compiled: List[Tuple[Dict[str, Any], re.Pattern[str]]] = [
        (r, re.compile(_route_to_pattern(r["path"]))) for r in routes
    ]
    # Build initial route entries.
    table: Dict[str, Dict[str, Any]] = {}
    for r in routes:
        # Disambiguate by method when the same path serves multiple verbs.
        key = r["path"]
        if key in table and table[key]["method"] != r["method"]:
            key = f"{r['method']} {r['path']}"
        table[key] = {
            "method": r["method"],
            "handler": r["handler"],
            "file": r["file"],
            "line": r["line"],
            "callers_js": [],
        }

    # Build a parallel key map so we can re-find the entry after match.
    def _lookup_key(route: Dict[str, Any]) -> str:
        # Prefer plain path; fall back to method-prefixed.
        if route["path"] in table and table[route["path"]]["handler"] == route["handler"]:
            return route["path"]
        return f"{route['method']} {route['path']}"

    for call in js_calls:
        prefix = _normalise_for_match(call["prefix"])
        verb = call.get("verb") or "fetch"
        for route, pat in compiled:
            if verb != "fetch" and verb.upper() != route["method"]:
                continue
            if pat.match(prefix):
                key = _lookup_key(route)
                bucket = table[key]["callers_js"]
                rec = {"file": call["file"], "line": call["line"], "raw": call["raw"]}
                # Dedupe.
                if rec not in bucket:
                    bucket.append(rec)
                # First match wins (avoids double-counting when two
                # routes share a prefix; the regex anchoring above
                # already guards most overlaps).
                break

    # Stable order for callers list.
    for entry in table.values():
        entry["callers_js"].sort(key=lambda c: (c["file"], c["line"]))
    return table


# ----------------------------------------------------------------------
# Build entry point
# ----------------------------------------------------------------------


def build_route_index(project_root: str) -> Dict[str, Dict[str, Any]]:
    routes = collect_python_routes(project_root)
    js_calls = collect_js_calls(project_root)
    table = match_calls_to_routes(routes, js_calls)

    # Augment with Python call sites that go through a project-internal
    # HTTP wrapper (e.g. bot.api_client.get_client). Configured via
    # ``.vc-context/conventions.json`` → ``http_clients``. Empty config
    # = no augmentation, no error.
    from http_callers import (  # type: ignore[import-not-found]
        attach_python_callers,
        collect_python_calls,
        load_http_clients,
    )

    specs = load_http_clients(project_root)
    if specs:
        py_calls = collect_python_calls(project_root, specs)
        if py_calls:
            attach_python_callers(table, py_calls)
    return table


def write_route_index(project_root: str, index: Dict[str, Any]) -> str:
    out_path = os.path.join(project_root, ROUTES_FILENAME)
    ordered = {k: index[k] for k in sorted(index)}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return out_path


# ----------------------------------------------------------------------
# Reverse query helpers (used by QueryEngine)
# ----------------------------------------------------------------------


def find_route_for_path(index: Dict[str, Dict[str, Any]], path: str) -> Optional[Dict[str, Any]]:
    """Return the route record for a given URL path (or method-prefixed key).

    Lookup order: exact key → bare path → first method-prefixed match.
    """
    if not path:
        return None
    if path in index:
        entry = dict(index[path])
        entry["path"] = path
        return entry
    # Strip an optional leading "METHOD " — caller may have passed
    # "GET /api/foo" or just "/api/foo".
    parts = path.split(" ", 1)
    if len(parts) == 2 and parts[0].isupper():
        verb, raw = parts
        for key, entry in index.items():
            if entry.get("method") == verb and (key == raw or key.endswith(" " + raw)):
                out = dict(entry)
                out["path"] = raw
                return out
    return None


def callers_for_route(index: Dict[str, Dict[str, Any]], path: str) -> List[Dict[str, Any]]:
    """Flat list of every call-site that hits ``path`` — JS *and* Python.

    Each record carries a ``lang`` field (``"js"`` / ``"python"``) so
    callers can group/filter without consulting two structures. Older
    consumers reading ``entry.callers_js`` directly are unaffected —
    this helper just merges both buckets at query time.
    """
    entry = find_route_for_path(index, path)
    if entry is None:
        return []
    out: List[Dict[str, Any]] = []
    for c in entry.get("callers_js") or ():
        rec = dict(c)
        rec["lang"] = "js"
        out.append(rec)
    for c in entry.get("callers_python") or ():
        rec = dict(c)
        rec["lang"] = "python"
        out.append(rec)
    return out


def route_for_js_file(index: Dict[str, Dict[str, Any]], file_path: str) -> List[Dict[str, Any]]:
    """Every route entry whose JS callers include ``file_path``."""
    file_path = file_path.replace(os.sep, "/")
    out: List[Dict[str, Any]] = []
    for path, entry in index.items():
        for c in entry.get("callers_js") or ():
            if c.get("file") == file_path:
                rec = dict(entry)
                rec["path"] = path
                out.append(rec)
                break
    return out
