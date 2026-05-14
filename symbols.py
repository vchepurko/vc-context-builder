"""Symbol-level helpers for the agent context graph.

Two responsibilities:

1. **Decorator inspection** — recognise FastAPI / aiogram patterns on a
   function definition and emit a short role string.

2. **Cross-file scans** — find every function name registered as a
   scheduler job (`scheduler.add_job(<callable>, ...)`) so the role
   detector can tag them later.

Plus a tiny `path_role()` helper for file-path heuristics
(repository / service / api-client / migration).

Stdlib only — no third-party imports.
"""

from __future__ import annotations

import ast
import os
import re
from typing import Optional, Set

# ---------------------------------------------------------------------------
# Decorator-driven roles
# ---------------------------------------------------------------------------

# Decorator method names that mark a FastAPI HTTP route.
_FASTAPI_METHODS = {
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "options",
    "head",
    "trace",
    "api_route",
}

# Aiogram v3 router methods.
_AIOGRAM_METHODS = {
    "message",
    "callback_query",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "inline_query",
    "chosen_inline_result",
    "shipping_query",
    "pre_checkout_query",
    "poll",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "errors",
    "error",
}

# Decorator base names (`<base>.<method>`) that look like a FastAPI router.
_FASTAPI_BASES = {"app", "router", "api", "api_router"}


def _decorator_call_chain(dec: ast.AST) -> Optional[tuple[str, str]]:
    """Return ``(base, method)`` for ``@<base>.<method>(...)`` style
    decorators, else ``None``.
    """
    # `@router.get("/x")`  → Call(func=Attribute(value=Name, attr='get'))
    # `@router.get`        → Attribute(value=Name, attr='get')   (rare)
    func = dec.func if isinstance(dec, ast.Call) else dec
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    return (func.value.id, func.attr)


# Heuristic patterns used by `_classify_aiogram_message` below.
# A positional arg that looks like ``StateGroupName.field`` (capitalised
# identifier + lowercase attribute) is treated as an aiogram FSM filter.
_FSM_STATE_REF_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


def _decorator_filter_args_text(dec: ast.AST) -> list:
    """Return ``ast.unparse`` text of every positional and keyword-arg value
    of a decorator call. Empty list when the decorator is bare.

    Used to peek at aiogram message/callback filters without re-implementing
    the whole expression matcher — we just look at the text.
    """
    if not isinstance(dec, ast.Call):
        return []
    out = []
    for a in dec.args:
        try:
            out.append(ast.unparse(a))
        except Exception:
            pass
    for kw in dec.keywords or ():
        try:
            out.append(ast.unparse(kw.value))
        except Exception:
            pass
    return out


def _classify_aiogram_message(dec: ast.Call) -> str:
    """Pick the most specific role for ``@router.message(...)``.

    Order — most specific first:

    * ``catch-all-handler``    — bare ``@router.message()`` (no args).
    * ``command-handler``      — first arg is ``Command(...)`` / ``CommandStart(...)``.
    * ``fsm-message-handler``  — any arg looks like ``XxxState.field`` (StatesGroup ref).
    * ``text-match-handler``   — uses ``F.text...`` filters and isn't FSM-bound.
    * ``aiogram-handler``      — fallback for filters we don't classify.
    """
    args_text = _decorator_filter_args_text(dec)
    if not args_text:
        return "catch-all-handler"

    # Command(...) / CommandStart(...)
    for txt in args_text:
        head = txt.split("(", 1)[0]
        if head in {"Command", "CommandStart", "CommandObject"}:
            return "command-handler"

    # FSM state reference (e.g. AddStaffState.waiting_user_id). We skip
    # ``F`` / ``F.<...>`` so a ``F.text`` filter doesn't get misclassified
    # — `F` is always magic-filter, never a StatesGroup.
    for txt in args_text:
        if txt == "F" or txt.startswith("F.") or txt.startswith("F("):
            continue
        if _FSM_STATE_REF_RE.match(txt):
            return "fsm-message-handler"

    # Magic-filter on text content.
    for txt in args_text:
        if txt == "F.text" or txt.startswith("F.text") or "F.text" in txt:
            return "text-match-handler"

    return "aiogram-handler"


def _classify_aiogram_callback(dec: ast.Call) -> str:
    """Pick the role for ``@router.callback_query(...)``.

    Currently flat — every ``callback_query`` decorator becomes a
    ``callback-handler``. FSM-bound callbacks exist but are rare; the
    role split lives mostly on the message side where the FSM/command
    distinction matters.
    """
    return "callback-handler"


def extract_decorator_roles(node: ast.AST) -> Optional[str]:
    """Inspect a FunctionDef / AsyncFunctionDef's decorator list and
    return a role string, or ``None`` if no pattern matches.

    Order of preference (a function can satisfy multiple patterns; we
    return the most specific one):

    1. Aiogram subroles — see ``_classify_aiogram_message`` /
       ``_classify_aiogram_callback`` for the precise split. Catches
       ``@router.callback_query(...)``, ``@router.message(...)``, and
       every other aiogram event method as a fallback ``aiogram-handler``.
    2. ``route`` — ``@router.get(...)``, ``@app.post(...)``.
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    decorators = getattr(node, "decorator_list", []) or []

    # Aiogram first — its method names overlap with nothing in FastAPI.
    for dec in decorators:
        chain = _decorator_call_chain(dec)
        if chain is None:
            continue
        base, method = chain
        if method not in _AIOGRAM_METHODS or base not in {"router", "dp"}:
            continue
        if not isinstance(dec, ast.Call):
            # Bare ``@router.method`` form (rare). Treat as catch-all
            # for ``message`` and as plain ``aiogram-handler`` otherwise.
            return "catch-all-handler" if method == "message" else "aiogram-handler"
        if method == "message":
            return _classify_aiogram_message(dec)
        if method == "callback_query":
            return _classify_aiogram_callback(dec)
        # Other aiogram events (edited_message, channel_post, ...) keep
        # the umbrella tag — a finer split would be ceremony for
        # rarely-used handler types.
        return "aiogram-handler"

    # Then FastAPI.
    for dec in decorators:
        chain = _decorator_call_chain(dec)
        if chain is None:
            continue
        base, method = chain
        if base in _FASTAPI_BASES and method in _FASTAPI_METHODS:
            return "route"

    return None


def is_states_group_class(node: ast.AST) -> bool:
    """Heuristic: ``True`` when ``node`` is ``class X(StatesGroup): ...``.

    Matches both ``StatesGroup`` (bare import) and ``aiogram.fsm.state.StatesGroup``
    (attribute form). False for non-class nodes.
    """
    if not isinstance(node, ast.ClassDef):
        return False
    for base in node.bases or ():
        if isinstance(base, ast.Name) and base.id == "StatesGroup":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "StatesGroup":
            return True
    return False


# ---------------------------------------------------------------------------
# Webhook heuristic (best-effort, name + signature shape)
# ---------------------------------------------------------------------------

_WEBHOOK_PARAM_TYPE_HINTS = {
    # Anything ending with `.Request` is treated as a webhook signal,
    # along with bare `Request`.
    "Request",
    "web.Request",
}


def is_webhook_function(node: ast.AST) -> bool:
    """Heuristic: true if a function looks like a payment / event webhook.

    Triggers:
        - name contains "webhook" or "callback" (case-insensitive), AND
        - takes a `request:` parameter typed as Request / web.Request.
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    name_l = node.name.lower()
    if "webhook" not in name_l and "callback" not in name_l:
        return False

    args = node.args
    all_args = list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs)
    for a in all_args:
        if a.arg != "request":
            continue
        ann = a.annotation
        if ann is None:
            # bare `request` param + name hint is enough to flag.
            return True
        try:
            txt = ast.unparse(ann)
        except Exception:
            continue
        if txt in _WEBHOOK_PARAM_TYPE_HINTS or txt.endswith(".Request"):
            return True
    return False


# ---------------------------------------------------------------------------
# Path-driven roles (file-path heuristics)
# ---------------------------------------------------------------------------


def _normalise(path: str) -> str:
    """Forward-slash, leading-./ stripped, lowercase."""
    p = path.replace(os.sep, "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def path_role(file_path: str) -> Optional[str]:
    """Return a role string derived purely from the file's location, or
    ``None`` if no path heuristic fires.

    Heuristics:
        - ``alembic/versions/*.py``       → ``migration``
        - ``database/repositories/*.py``  → ``repository``
        - ``services/*.py`` (incl. nested) → ``service``
        - ``bot/api_client/*.py``         → ``api-client``
    """
    p = _normalise(file_path)
    if not p.endswith(".py"):
        return None

    # Strip a leading repo-root prefix if present — we operate on the
    # tail only, so absolute paths work too.
    parts = p.split("/")

    # Look for the marker subsequence anywhere in the path.
    for i in range(len(parts) - 1):
        segment = parts[i : i + 2]
        if segment == ["alembic", "versions"]:
            return "migration"
        if segment == ["database", "repositories"]:
            return "repository"
        if segment == ["bot", "api_client"]:
            return "api-client"

    # `services/...` — match `services` as a directory, ignore standalone
    # `services.py` files (would be treated as a module elsewhere).
    if "services" in parts:
        idx = parts.index("services")
        # ensure there's at least one .py file after `services/` (i.e.
        # this isn't `something/services` as the basename).
        if idx < len(parts) - 1:
            return "service"

    return None


# ---------------------------------------------------------------------------
# Cross-file scheduler-job scan
# ---------------------------------------------------------------------------


def _is_scheduler_add_job_call(node: ast.AST) -> bool:
    """Match `<anything>.add_job(...)` — generous on the receiver name
    (could be `scheduler`, `self.scheduler`, etc).
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "add_job":
        return True
    return False


def _first_arg_callable_name(call: ast.Call) -> Optional[str]:
    """Best-effort extraction of the callable name that's being scheduled.

    Recognises:
        - `scheduler.add_job(my_func, ...)`     → "my_func"
        - `scheduler.add_job(mod.my_func, ...)` → "my_func"
        - `scheduler.add_job(func=my_func, ...)` (kwarg form) — also
          handled as a fallback for completeness.
    Returns ``None`` for lambdas, strings, or anything we can't resolve.
    """
    candidate: Optional[ast.AST] = None

    if call.args:
        candidate = call.args[0]
    else:
        for kw in call.keywords or ():
            if kw.arg in {"func", "callable"}:
                candidate = kw.value
                break

    if candidate is None:
        return None
    if isinstance(candidate, ast.Name):
        return candidate.id
    if isinstance(candidate, ast.Attribute):
        return candidate.attr
    return None


def extract_scheduler_jobs_from_codebase(
    root_dir: str,
    ignore_dirs: Optional[Set[str]] = None,
) -> Set[str]:
    """Single-pass AST scan of every ``.py`` file under ``root_dir``.

    Collects the set of callable *names* registered through any
    ``<obj>.add_job(<callable>, ...)`` call. Used downstream so the
    parser can tag those callables with ``role: scheduler-job``.
    """
    ignore = set(ignore_dirs or ()) | {
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
    }

    def should_ignore(d: str) -> bool:
        return d in ignore or d.startswith("venv")

    found: Set[str] = set()

    for cur, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if not should_ignore(d)]
        for f in files:
            if not f.endswith(".py"):
                continue
            full = os.path.join(cur, f)
            try:
                with open(full, encoding="utf-8") as fp:
                    src = fp.read()
            except OSError:
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for sub in ast.walk(tree):
                if not _is_scheduler_add_job_call(sub):
                    continue
                # Defensive narrow — survives `python -O` (which strips
                # `assert`). The check above already guaranteed Call
                # shape, but `if not isinstance` is the runtime-safe
                # idiom mypy also accepts as type narrowing.
                if not isinstance(sub, ast.Call):
                    continue
                name = _first_arg_callable_name(sub)
                if name:
                    found.add(name)
    return found
