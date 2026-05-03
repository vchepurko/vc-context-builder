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
from typing import Optional, Set


# ---------------------------------------------------------------------------
# Decorator-driven roles
# ---------------------------------------------------------------------------

# Decorator method names that mark a FastAPI HTTP route.
_FASTAPI_METHODS = {
    "get", "post", "put", "delete", "patch",
    "options", "head", "trace", "api_route",
}

# Aiogram v3 router methods.
_AIOGRAM_METHODS = {
    "message", "callback_query", "edited_message",
    "channel_post", "edited_channel_post",
    "inline_query", "chosen_inline_result",
    "shipping_query", "pre_checkout_query",
    "poll", "poll_answer", "my_chat_member",
    "chat_member", "chat_join_request",
    "errors", "error",
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


def extract_decorator_roles(node: ast.AST) -> Optional[str]:
    """Inspect a FunctionDef / AsyncFunctionDef's decorator list and
    return a role string, or ``None`` if no pattern matches.

    Order of preference (a function can satisfy multiple patterns; we
    return the most specific one):

    1. `aiogram-handler` — `@router.message(...)` etc.
    2. `route`           — `@router.get(...)`, `@app.post(...)`.
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
        if method in _AIOGRAM_METHODS and base in {"router", "dp"}:
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
        segment = parts[i:i + 2]
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
        ".git", "node_modules", "vendor", "__pycache__",
        "dist", "build", ".venv", "venv", ".idea", ".vscode",
    }

    found: Set[str] = set()

    for cur, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in ignore]
        for f in files:
            if not f.endswith(".py"):
                continue
            full = os.path.join(cur, f)
            try:
                with open(full, "r", encoding="utf-8") as fp:
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
                name = _first_arg_callable_name(sub)
                if name:
                    found.add(name)
    return found
