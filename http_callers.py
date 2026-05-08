"""Python HTTP-client call-site collector.

Companion to ``route_bridge.py``: where the latter parses JS/TS
``fetch(...)`` / ``.get(...)`` call sites, this module parses the
*Python* side — calls that go through a project-internal HTTP wrapper
(e.g. ``bot.api_client.get_client``) which the JS-style regex would
never catch.

Configured via ``.vc-context/conventions.json``::

    {
      "rules": [...],
      "http_clients": [
        {
          "factory": "bot.api_client.get_client",
          "methods": ["post", "get", "patch", "delete"],
          "first_arg_is_path": true
        }
      ]
    }

* ``factory`` — dotted path of the callable that returns a client.
* ``methods`` — verb names exposed on the returned client.
* ``first_arg_is_path`` — when ``true``, the first positional arg of
  each method call is the URL path. Default ``true``. (Reserved for
  future client shapes that pass the path as a kwarg.)

Two call shapes are recognised inside each Python file:

1. **Inline** — ``get_client().post("/api/x", ...)``
   (the canonical aiogram-style form).

2. **Variable-bound** — ``c = get_client(); c.post("/api/x", ...)``
   when ``c`` is assigned from a single ``factory()`` call in the
   same enclosing function. Cross-function tracking is intentionally
   skipped — a real type tracer would over-engineer this.

Stdlib only.
"""

from __future__ import annotations

import ast
import json
import os
from collections.abc import Iterable
from typing import Any, Dict, List, Optional, Set

CONFIG_RELATIVE_PATH = os.path.join(".vc-context", "conventions.json")

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
# Config
# ----------------------------------------------------------------------


class HttpClientSpec:
    """Resolved entry from the ``http_clients`` config block."""

    __slots__ = ("factory_module", "factory_name", "first_arg_is_path", "methods")

    def __init__(
        self,
        factory_module: str,
        factory_name: str,
        methods: Set[str],
        first_arg_is_path: bool,
    ) -> None:
        self.factory_module = factory_module
        self.factory_name = factory_name
        self.methods = methods
        self.first_arg_is_path = first_arg_is_path


def load_http_clients(project_root: str) -> List[HttpClientSpec]:
    """Read the ``http_clients`` block from ``conventions.json``.

    Missing file / missing block / malformed entry → empty list. The
    builder's other artifacts must keep working when this isn't
    configured.
    """
    config_path = os.path.join(project_root, CONFIG_RELATIVE_PATH)
    if not os.path.isfile(config_path):
        return []
    try:
        with open(config_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    raw_list = data.get("http_clients")
    if not isinstance(raw_list, list):
        return []

    out: List[HttpClientSpec] = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        factory = entry.get("factory")
        if not isinstance(factory, str) or "." not in factory:
            continue
        methods = entry.get("methods")
        if not isinstance(methods, list) or not methods:
            continue
        method_set = {str(m).lower() for m in methods if isinstance(m, str)}
        if not method_set:
            continue
        first_arg = entry.get("first_arg_is_path", True)
        if not isinstance(first_arg, bool):
            first_arg = True
        module, name = factory.rsplit(".", 1)
        out.append(HttpClientSpec(module, name, method_set, first_arg))
    return out


# ----------------------------------------------------------------------
# AST helpers
# ----------------------------------------------------------------------


def _iter_python_files(project_root: str) -> Iterable[str]:
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(cur, f)


def _factory_aliases(tree: ast.AST, spec: HttpClientSpec) -> Set[str]:
    """Names used in this file to refer to ``spec.factory_module.spec.factory_name``.

    Tracks both ``from <module> import <name>`` and ``from <module>
    import <name> as <alias>``. ``import <module>`` (then ``module.name(...)``)
    is rare for a thin factory and intentionally skipped — keeps the
    parser focused on the common case.
    """
    aliases: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != spec.factory_module:
            continue
        for alias in node.names:
            if alias.name != spec.factory_name:
                continue
            aliases.add(alias.asname or alias.name)
    return aliases


def _string_arg(call: ast.Call, kind_first_arg: bool) -> Optional[str]:
    """Return the URL-path string from a method call, or ``None``.

    ``kind_first_arg=True`` → first positional arg.
    """
    if kind_first_arg and call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _enclosing_function_name(node: ast.AST, parents: Dict[int, ast.AST]) -> Optional[str]:
    """Walk up the parent chain to find the nearest FunctionDef name."""
    cur: Optional[ast.AST] = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = parents.get(id(cur))
    return None


def _build_parents(tree: ast.AST) -> Dict[int, ast.AST]:
    """Map ``id(child) → parent`` for every node in ``tree``."""
    parents: Dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _is_factory_call(node: ast.AST, factory_aliases: Set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in factory_aliases
    )


def _client_var_assignments(
    tree: ast.AST,
    factory_aliases: Set[str],
) -> Dict[str, Set[str]]:
    """Return ``{function_name: {var_name, ...}}`` of locals assigned
    from a factory call.

    Naive on purpose — we don't track reassignment, scope shadowing,
    or attribute targets (``self.client = get_client()``). Those are
    rare in handler code and not worth a real flow analysis.
    """
    out: Dict[str, Set[str]] = {}
    parents = _build_parents(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not _is_factory_call(node.value, factory_aliases):
            continue
        # Only single-target ``c = get_client()`` for now.
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        fn = _enclosing_function_name(node, parents)
        if fn is None:
            continue
        out.setdefault(fn, set()).add(target.id)
    return out


# ----------------------------------------------------------------------
# Main collection
# ----------------------------------------------------------------------


def collect_python_calls(
    project_root: str,
    specs: List[HttpClientSpec],
) -> List[Dict[str, Any]]:
    """Walk every ``.py`` file once and return URL-path call sites.

    Each record: ``{path, verb, file, line, raw, function}``.

    * ``path`` — the literal string passed in (already normalised:
      starts with ``/``).
    * ``verb`` — method name (``post`` / ``get`` / ...) — lowercase.
    * ``raw`` — verbatim path argument (same as ``path`` today; kept
      separate to mirror ``callers_js`` and to leave room for f-string
      stripping later).
    * ``function`` — name of the enclosing function/coroutine, or
      ``"<module>"`` for module-level calls.
    """
    if not specs:
        return []

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

        rel = os.path.relpath(full, project_root).replace(os.sep, "/")
        parents = _build_parents(tree)

        for spec in specs:
            aliases = _factory_aliases(tree, spec)
            if not aliases:
                continue

            # Locals bound to factory() calls, keyed by enclosing fn.
            client_vars = _client_var_assignments(tree, aliases)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                method = func.attr.lower()
                if method not in spec.methods:
                    continue

                receiver = func.value
                # Inline form: factory().method(...)
                inline = _is_factory_call(receiver, aliases)
                # Variable-bound form: c.method(...) with c assigned earlier
                var_bound = False
                if not inline and isinstance(receiver, ast.Name):
                    fn_name = _enclosing_function_name(node, parents)
                    if fn_name and receiver.id in client_vars.get(fn_name, set()):
                        var_bound = True
                if not (inline or var_bound):
                    continue

                path = _string_arg(node, spec.first_arg_is_path)
                if not path or not path.startswith("/"):
                    continue

                fn_name = _enclosing_function_name(node, parents) or "<module>"
                rec = {
                    "path": path,
                    "verb": method,
                    "file": rel,
                    "line": getattr(node, "lineno", 0),
                    "raw": path,
                    "function": fn_name,
                }
                out.append(rec)

    out.sort(key=lambda r: (r["file"], r["line"]))
    return out


# ----------------------------------------------------------------------
# Match python calls to existing route table
# ----------------------------------------------------------------------


def attach_python_callers(
    route_table: Dict[str, Dict[str, Any]],
    py_calls: List[Dict[str, Any]],
) -> None:
    """Mutate ``route_table`` in place: add ``callers_python`` arrays.

    A python call at ``/api/foo`` matches the route entry whose
    pattern matches that path and whose method agrees with the call's
    verb. Mirrors ``match_calls_to_routes`` from ``route_bridge`` but
    for the simpler case (no template stripping needed — Python paths
    are already literals).
    """
    # Lazy import to avoid a circular dependency on the consumer side
    # (route_bridge imports us when assembling its index).
    import re

    from route_bridge import _route_to_pattern  # type: ignore[import-not-found]

    compiled = []
    for key, entry in route_table.items():
        compiled.append((key, entry, re.compile(_route_to_pattern(_path_only(key)))))

    for call in py_calls:
        path = call["path"]
        verb = call.get("verb", "").upper()
        for _key, entry, pat in compiled:
            if entry.get("method") and verb and entry["method"] != verb:
                continue
            if pat.match(path):
                bucket = entry.setdefault("callers_python", [])
                rec = {
                    "file": call["file"],
                    "line": call["line"],
                    "raw": call["raw"],
                    "function": call.get("function") or "<module>",
                }
                if rec not in bucket:
                    bucket.append(rec)
                break
    # Stable ordering inside each new bucket so artifacts diff cleanly.
    for entry in route_table.values():
        if "callers_python" in entry:
            entry["callers_python"].sort(key=lambda c: (c["file"], c["line"]))


def _path_only(key: str) -> str:
    """Strip an optional ``METHOD `` prefix from a route table key."""
    parts = key.split(" ", 1)
    if len(parts) == 2 and parts[0].isupper():
        return parts[1]
    return key


# ----------------------------------------------------------------------
# Reverse query helper for QueryEngine
# ----------------------------------------------------------------------


def python_callers_for_route(
    index: Dict[str, Dict[str, Any]],
    path: str,
) -> List[Dict[str, Any]]:
    """``[{file, line, raw, function}, ...]`` for ``path`` (or empty)."""
    from route_bridge import find_route_for_path  # type: ignore[import-not-found]

    entry = find_route_for_path(index, path)
    if entry is None:
        return []
    return list(entry.get("callers_python") or [])
