"""Aiogram FSM flow graph (Feature F).

Walks every Python file once and assembles the lifecycle of every
``StatesGroup.field`` declared in the project::

    {
      "AddStaffState.waiting_user_id": {
        "state_class": {"file": "bot/states/admin_states.py", "line": 107},
        "entered_by":  [{handler, file, line, callback?}, ...],
        "consumed_by": [{handler, file, line, kind, filter?}, ...]
      },
      ...
    }

* ``state_class`` — where the ``class XxxState(StatesGroup): field = State()``
  is declared.
* ``entered_by`` — handlers that set this state via ``state.set_state(X.f)``.
  When the entering handler is a ``callback_query``, the matching
  ``F.data`` literal/prefix is folded in as ``callback`` so an agent
  can answer "which button starts this flow?" in one read.
* ``consumed_by`` — handlers whose decorator filter is the state ref
  itself (``@router.message(X.f, ...)`` / ``@router.callback_query(X.f, ...)``).

Only intra-file ``set_state`` calls and decorator-level state refs are
captured. Cross-file jumps via shared helpers are out of scope — they
are rare and not worth the analysis budget.

Stdlib only.
"""

from __future__ import annotations

import ast
import json
import os
from typing import Any, Optional
from collections.abc import Iterable


FSM_FLOW_FILENAME = "agent_fsm_flows.json"

IGNORE_DIRS = {
    ".git", "node_modules", "vendor", "__pycache__",
    "dist", "build", ".venv", "venv", ".idea", ".vscode",
    ".ai-context", ".vc-context",
}


# ----------------------------------------------------------------------
# AST walkers
# ----------------------------------------------------------------------

def _iter_python_files(project_root: str) -> Iterable[str]:
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(cur, f)


def _is_states_group(node: ast.ClassDef) -> bool:
    """Mirror of ``symbols.is_states_group_class`` — local copy keeps
    this module zero-dependency on the symbols layer.
    """
    for base in node.bases or ():
        if isinstance(base, ast.Name) and base.id == "StatesGroup":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "StatesGroup":
            return True
    return False


def collect_state_groups(
    project_root: str,
) -> dict[str, dict[str, Any]]:
    """Return ``{full_name → {state_class}}`` for every ``StatesGroup`` field.

    ``full_name`` is ``ClassName.field_name`` so it matches what the
    handlers reference (``AddStaffState.waiting_user_id``). The value
    is a stub the rest of the pipeline fills in — entered_by /
    consumed_by lists start empty.
    """
    out: dict[str, dict[str, Any]] = {}
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
            if not isinstance(node, ast.ClassDef):
                continue
            if not _is_states_group(node):
                continue
            for stmt in node.body:
                # ``waiting_user_id = State()``  → ast.Assign
                # ``waiting_user_id: State = State()``  → ast.AnnAssign
                targets: list[ast.AST] = []
                if isinstance(stmt, ast.Assign):
                    targets = list(stmt.targets)
                elif isinstance(stmt, ast.AnnAssign) and stmt.target is not None:
                    targets = [stmt.target]
                else:
                    continue
                for tgt in targets:
                    if not isinstance(tgt, ast.Name):
                        continue
                    if tgt.id.startswith("_"):
                        continue
                    full_name = f"{node.name}.{tgt.id}"
                    if full_name in out:
                        continue  # first definition wins, keeps determinism
                    out[full_name] = {
                        "state_class": {"file": rel, "line": node.lineno},
                        "entered_by": [],
                        "consumed_by": [],
                    }
    return out


# ----------------------------------------------------------------------
# Decorator inspection
# ----------------------------------------------------------------------

def _aiogram_router_method(dec: ast.AST) -> Optional[str]:
    """Return ``"message"`` / ``"callback_query"`` / etc. when ``dec`` is
    ``@router.<method>(...)`` / ``@dp.<method>(...)``, else ``None``.
    """
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    if func.value.id not in {"router", "dp"}:
        return None
    return func.attr


def _state_ref_from_arg(arg: ast.AST) -> Optional[str]:
    """``Attribute(value=Name(id='X'), attr='y')`` → ``"X.y"`` (or ``None``).

    Skips ``F.<...>`` and ``Command(...)`` shapes — they're not state refs.
    """
    if not isinstance(arg, ast.Attribute):
        return None
    if not isinstance(arg.value, ast.Name):
        return None
    if arg.value.id == "F":
        return None
    return f"{arg.value.id}.{arg.attr}"


def _f_data_filter(dec: ast.Call) -> Optional[tuple[str, str]]:
    """If decorator filters on ``F.data``, return ``(kind, value)``.

    ``kind`` is ``"exact"`` or ``"prefix"``. Returns ``None`` for any
    other shape.
    """
    for arg in dec.args:
        # F.data == "x"
        if (
            isinstance(arg, ast.Compare)
            and len(arg.ops) == 1
            and isinstance(arg.ops[0], ast.Eq)
            and isinstance(arg.left, ast.Attribute)
            and arg.left.attr == "data"
            and isinstance(arg.left.value, ast.Name)
            and arg.left.value.id == "F"
            and len(arg.comparators) == 1
            and isinstance(arg.comparators[0], ast.Constant)
            and isinstance(arg.comparators[0].value, str)
        ):
            return ("exact", arg.comparators[0].value)
        # F.data.startswith("x")
        if (
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "startswith"
            and isinstance(arg.func.value, ast.Attribute)
            and arg.func.value.attr == "data"
            and isinstance(arg.func.value.value, ast.Name)
            and arg.func.value.value.id == "F"
            and arg.args
            and isinstance(arg.args[0], ast.Constant)
            and isinstance(arg.args[0].value, str)
        ):
            return ("prefix", arg.args[0].value)
    return None


def _decorator_filter_summary(dec: ast.Call) -> Optional[str]:
    """Short text for the consumed_by record's ``filter`` field.

    Joins every non-state arg with `,` so an agent reading the record
    sees what else gates the handler. ``None`` when only the state
    ref was passed.
    """
    parts: list[str] = []
    for arg in dec.args:
        if _state_ref_from_arg(arg) is not None:
            continue
        try:
            parts.append(ast.unparse(arg))
        except Exception:
            pass
    if not parts:
        return None
    return ",".join(parts)


# ----------------------------------------------------------------------
# set_state detection
# ----------------------------------------------------------------------

def _set_state_targets(func_body: list[ast.AST]) -> list[str]:
    """Return every ``X.y`` reference passed to a ``state.set_state(...)``
    call inside ``func_body``.

    Catches both ``state.set_state(...)`` and ``await state.set_state(...)``.
    Ignores dynamic / non-attribute arguments.
    """
    out: list[str] = []
    for stmt in func_body:
        for sub in ast.walk(stmt):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != "set_state":
                continue
            if not sub.args:
                continue
            ref = _state_ref_from_arg(sub.args[0])
            if ref is not None:
                out.append(ref)
    return out


# ----------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------

def collect_fsm_flow(project_root: str) -> dict[str, dict[str, Any]]:
    """Build the full ``{state_full_name → record}`` index in one pass."""
    flows = collect_state_groups(project_root)
    if not flows:
        return {}

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

            # consumed_by — decorator argument is a state ref.
            for dec in node.decorator_list or ():
                method = _aiogram_router_method(dec)
                if method is None:
                    continue
                if not isinstance(dec, ast.Call):
                    continue
                state_arg: Optional[str] = None
                for arg in dec.args:
                    state_arg = _state_ref_from_arg(arg)
                    if state_arg is not None:
                        break
                if state_arg is None or state_arg not in flows:
                    continue
                rec: dict[str, Any] = {
                    "handler": node.name,
                    "file": rel,
                    "line": node.lineno,
                    "kind": method,
                }
                summary = _decorator_filter_summary(dec)
                if summary:
                    rec["filter"] = summary
                bucket = flows[state_arg]["consumed_by"]
                if rec not in bucket:
                    bucket.append(rec)

            # entered_by — body has ``state.set_state(X.y)``.
            targets = _set_state_targets(node.body)
            for target in targets:
                if target not in flows:
                    continue
                # Pick the entering callback_data when this handler is
                # a ``callback_query`` — that's the button that starts
                # the flow.
                callback: Optional[str] = None
                for dec in node.decorator_list or ():
                    method = _aiogram_router_method(dec)
                    if method != "callback_query":
                        continue
                    if not isinstance(dec, ast.Call):
                        continue
                    parsed = _f_data_filter(dec)
                    if parsed is not None:
                        callback = parsed[1]
                        break
                rec = {
                    "handler": node.name,
                    "file": rel,
                    "line": node.lineno,
                }
                if callback is not None:
                    rec["callback"] = callback
                bucket = flows[target]["entered_by"]
                if rec not in bucket:
                    bucket.append(rec)

    # Stable order inside each bucket so artifacts diff cleanly.
    for record in flows.values():
        record["entered_by"].sort(key=lambda r: (r["file"], r["line"], r["handler"]))
        record["consumed_by"].sort(key=lambda r: (r["file"], r["line"], r["handler"]))
    return flows


def write_fsm_flow(project_root: str, index: dict[str, dict[str, Any]]) -> str:
    out_path = os.path.join(project_root, FSM_FLOW_FILENAME)
    ordered = {k: index[k] for k in sorted(index)}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return out_path


# ----------------------------------------------------------------------
# Lookup
# ----------------------------------------------------------------------

def trace_fsm_flow(
    index: dict[str, dict[str, Any]],
    state: str,
) -> Optional[dict[str, Any]]:
    """Resolve a state name to its flow record.

    Accepts both the full form (``AddStaffState.waiting_user_id``) and
    the short field name (``waiting_user_id``) when it's unambiguous.
    Returns ``None`` for unknown states or ambiguous short matches.
    """
    if not state:
        return None
    if state in index:
        return _shallow(index[state], state)

    matches = [k for k in index if k.split(".", 1)[-1] == state]
    if len(matches) == 1:
        return _shallow(index[matches[0]], matches[0])
    return None


def _shallow(record: dict[str, Any], full_name: str) -> dict[str, Any]:
    out: dict[str, Any] = {"state": full_name}
    out.update({k: list(v) if isinstance(v, list) else dict(v)
                for k, v in record.items()})
    return out
