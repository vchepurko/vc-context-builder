"""Class inspector — fields/methods/bases of a Python class.

Looks up the defining file in ``agent_symbols.json``, then AST-walks
that file to collect the class members. Project-agnostic: works for
SQLAlchemy models, pydantic BaseModel subclasses, dataclasses, plain
classes — anywhere annotations or `<name> = value` lines describe
fields and ``def`` blocks describe methods.

Stdlib only.
"""

from __future__ import annotations

import ast
import os
from typing import Any, Dict, List, Optional


def _first_line(doc: Optional[str]) -> Optional[str]:
    if not doc:
        return None
    for raw in doc.splitlines():
        line = raw.strip()
        if line:
            return line[:200]
    return None


def _summarise_field(node: ast.AST) -> Optional[Dict[str, Any]]:
    """Pull ``name: Type = default`` (or bare ``name = default``) out of
    a class body. Returns ``None`` for anything that isn't a single-
    target assignment.
    """
    # ``x: Type = value``  →  AnnAssign(target=Name, annotation, value?)
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        try:
            type_str = ast.unparse(node.annotation) if node.annotation else None
        except Exception:
            type_str = None
        try:
            default_str = ast.unparse(node.value) if node.value is not None else None
        except Exception:
            default_str = None
        return {
            "name": node.target.id,
            "type": type_str,
            "default": default_str,
            "line": getattr(node, "lineno", 0),
        }
    # ``x = value`` (no annotation; e.g. dataclass field with field(...) default)
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        try:
            default_str = ast.unparse(node.value)
        except Exception:
            default_str = None
        return {
            "name": node.targets[0].id,
            "type": None,
            "default": default_str,
            "line": getattr(node, "lineno", 0),
        }
    return None


def _summarise_method(node: ast.AST) -> Optional[Dict[str, Any]]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    # Skip private (`_x`) but keep dunders (`__init__`, `__repr__`).
    if node.name.startswith("_") and not (node.name.startswith("__") and node.name.endswith("__")):
        return None
    try:
        params_str = "(" + ast.unparse(node.args) + ")"
    except Exception:
        params_str = "(...)"
    decorators: List[str] = []
    for dec in getattr(node, "decorator_list", []) or ():
        try:
            decorators.append("@" + ast.unparse(dec))
        except Exception:
            pass
    out: Dict[str, Any] = {
        "name": node.name,
        "kind": "async-func" if isinstance(node, ast.AsyncFunctionDef) else "func",
        "params": params_str,
        "doc": _first_line(ast.get_docstring(node)),
        "line": getattr(node, "lineno", 0),
    }
    if decorators:
        out["decorators"] = decorators
    return out


def inspect_class(
    project_root: str,
    name: str,
    symbols: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve a class by ``name`` and return a structured summary.

    Steps:

    1. Use ``agent_symbols.json`` (passed in via ``symbols``) to find
       the defining file. Returns ``None`` if the symbol is unknown or
       isn't a class.
    2. Open and parse the file; locate the matching ``ast.ClassDef``.
    3. Collect ``bases`` (textual), ``fields`` (AnnAssign / Assign at
       class scope), ``methods`` (FunctionDef / AsyncFunctionDef with
       params + first-line docstring + decorator chain).

    Result shape:

        {
          "name", "file", "line", "doc",
          "bases":   ["Base", ...],
          "fields":  [{"name", "type", "default", "line"}, ...],
          "methods": [{"name", "kind", "params", "doc", "line",
                        "decorators"?}, ...]
        }
    """
    if not name:
        return None
    file_rel: Optional[str] = None
    if symbols:
        entry = symbols.get(name)
        if isinstance(entry, dict) and entry.get("kind") == "class":
            file_rel = entry.get("file")
    if not file_rel:
        return None
    full = os.path.join(project_root, file_rel)
    try:
        with open(full, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return None

    target: Optional[ast.ClassDef] = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            target = node
            break
    if target is None:
        return None

    bases: List[str] = []
    for base in target.bases or ():
        try:
            bases.append(ast.unparse(base))
        except Exception:
            pass

    fields: List[Dict[str, Any]] = []
    methods: List[Dict[str, Any]] = []
    for stmt in target.body:
        f = _summarise_field(stmt)
        if f is not None:
            fields.append(f)
            continue
        m = _summarise_method(stmt)
        if m is not None:
            methods.append(m)

    return {
        "name": name,
        "file": file_rel,
        "line": target.lineno,
        "doc": _first_line(ast.get_docstring(target)),
        "bases": bases,
        "fields": fields,
        "methods": methods,
    }
