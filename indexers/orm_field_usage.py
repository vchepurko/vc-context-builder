"""AST walker for ORM ``Model.column`` access patterns.

Closes another grep-fallback gap: "every read/write of
``Product.photo_file_id``" today returns 30+ lines of noise from
``grep -rn photo_file_id`` (mixes column references with unrelated
strings, comments, locale keys). This walker focuses on **Python
attribute access** where the parent is a known ORM model name —
``Product.photo_file_id``, ``order.user_id`` (when ``order`` was
typed/assigned from a model) — and ignores everything else.

Lossy by design
---------------
* Static AST walk; we don't try to resolve assignments through
  function calls or imports. ``model_instance.column`` matches only
  if the variable name appears in our hint list (``model_lower``) OR
  the access is via the model class directly.
* Doesn't follow ``getattr(model, "column")`` — agents who write
  this dynamically are off the indexed path by definition.
* Returns ``read`` vs ``write`` based on the immediate parent: load
  context (``ast.Load``) = read, store/aug-assign = write. Useful for
  refactor scoping ("what writes to ``Order.status``?").

Output
------
List of dicts, each match::

    {"file": "bot/handlers/orders.py", "line": 42, "kind": "read",
     "context": "order.photo_file_id"}

Stdlib only.
"""

from __future__ import annotations

import ast
import os
from typing import Any, Dict, FrozenSet, List, Optional

_IGNORE_DIRS: FrozenSet[str] = frozenset(
    {
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
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        "alembic",  # migrations reference columns by string in op.add_column — too noisy
    }
)

_MAX_BYTES_PER_FILE = 1024 * 1024


def find_usage(
    project_root: str,
    model: str,
    column: str,
    *,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Walk every ``.py`` file under ``project_root`` and return each
    attribute access matching ``<model_or_lowercased>.column``.

    Parameters
    ----------
    project_root:
        Absolute project root.
    model:
        ORM class name, e.g. ``Product`` or ``Order``. Case-sensitive
        for the class form; the lowercase form (``product.x``) is also
        matched because variable names typically follow the model
        name (``product = Product(...)``).
    column:
        Column attribute name, e.g. ``photo_file_id``. Exact match
        (case-sensitive — column names are stable identifiers).
    limit:
        Bound on the returned list. Short-circuits the walk once
        reached so a wide column name doesn't materialise thousands
        of hits.

    Returns
    -------
    list of ``{file, line, kind, context}``. ``kind`` is ``"read"`` or
    ``"write"``. ``context`` is the ``a.b`` source slice.
    """
    if not model or not column:
        return []

    model_lower = model[0].lower() + model[1:] if model else ""
    # Both forms: ``Model.column`` (class access) and ``model.column``
    # (instance access via canonical variable name).
    receivers = {model, model_lower} if model_lower else {model}

    out: List[Dict[str, Any]] = []
    project_root = os.path.abspath(project_root)

    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            abs_path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(abs_path) > _MAX_BYTES_PER_FILE:
                    continue
            except OSError:
                continue
            try:
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    source = fh.read()
            except OSError:
                continue
            try:
                tree = ast.parse(source, filename=abs_path)
            except SyntaxError:
                continue

            rel_path = os.path.relpath(abs_path, project_root)
            _collect_from_tree(tree, source, rel_path, receivers, column, out)
            if len(out) >= limit:
                return out[:limit]

    return out


def _collect_from_tree(
    tree: ast.AST,
    source: str,
    rel_path: str,
    receivers: set,
    column: str,
    out: List[Dict[str, Any]],
) -> None:
    """Walk ``tree``, append matches to ``out``."""
    src_lines = source.splitlines()
    # Track write-context nodes so we can classify ``Attribute`` nodes
    # nested inside them. ast.walk doesn't preserve parent, so we
    # collect ids of Attribute nodes that appear on the LHS of an
    # Assign / AugAssign / AnnAssign target tree first.
    write_ids = _collect_write_target_ids(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr != column:
            continue
        receiver = _attribute_receiver_name(node)
        if receiver not in receivers:
            continue

        kind = "write" if id(node) in write_ids else "read"
        line = node.lineno
        context = _source_slice(src_lines, node)
        out.append({"file": rel_path, "line": line, "kind": kind, "context": context})


def _collect_write_target_ids(tree: ast.AST) -> set:
    """Return ids of ``Attribute`` nodes that appear on the LHS of an
    assignment. Pre-walked once so the main pass is O(1) per Attribute.
    """
    write_ids: set = set()
    for node in ast.walk(tree):
        targets: List[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for t in targets:
            for sub in ast.walk(t):
                if isinstance(sub, ast.Attribute):
                    write_ids.add(id(sub))
    return write_ids


def _attribute_receiver_name(node: ast.Attribute) -> Optional[str]:
    """The receiver identifier of an attribute access. For ``a.b.c``
    returns ``a`` only when the access is ``Name.Attribute`` — we
    don't follow chains. Returns ``None`` for call results, subscripts,
    etc., which keeps the match precise."""
    if isinstance(node.value, ast.Name):
        return node.value.id
    return None


def _source_slice(src_lines: List[str], node: ast.Attribute) -> str:
    """The ``a.b`` source text the match points to. Capped to one
    line — Attribute nodes are rarely multi-line in real code."""
    lineno = node.lineno
    if 1 <= lineno <= len(src_lines):
        line = src_lines[lineno - 1].strip()
        # Cap context to keep tokens cheap — agent can read_slice for more.
        return line[:200]
    return ""
