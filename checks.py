"""Whitelisted command runner — `run_check(name)`.

Project-agnostic: the user declares safe-to-run commands in
``.vc-context/conventions.json``::

    {
      "checks": {
        "test":         ["uv", "run", "pytest", "-q"],
        "test-unit":    ["uv", "run", "pytest", "-q", "-m", "not integration"],
        "lint":         ["uv", "run", "ruff", "check"],
        "typecheck":    ["uv", "run", "mypy", "."]
      }
    }

Each value is either a **list of argv tokens** or an object with
``{"cmd": [...], "args_policy": {...}}``. The list form is fixed:
no extra arguments are accepted. The object form may accept
``run_check(..., args=[...])`` when every extra token is allowed by
the declared policy. All execution is still argv-only — no shell, no
string splitting, no injection. The runner executes the command with
``subprocess.run(args, cwd=project_root, timeout=...)``, captures
stdout+stderr, returns a structured result with the LAST ~50 lines
of each (full output truncated to keep MCP tool responses small).

Why a whitelist (not arbitrary command exec)? An MCP server runs
inside a developer's editor with the same privileges as the user;
exposing arbitrary shell to an LLM is a footgun. The whitelist makes
it explicit which commands the project considers safe to invoke
from automation, and refusing anything else.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple, cast

CONFIG_RELATIVE_PATH = os.path.join(".vc-context", "conventions.json")

_TAIL_LINES = 50  # max lines kept from each of stdout / stderr
_DEFAULT_TIMEOUT_SEC = 300


CheckSpec = Dict[str, Any]


def _load_config(project_root: str) -> Dict[str, Any]:
    path = os.path.join(project_root, CONFIG_RELATIVE_PATH)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _valid_argv(cmd: Any) -> bool:
    return isinstance(cmd, list) and bool(cmd) and all(isinstance(tok, str) and tok for tok in cmd)


def load_checks(project_root: str) -> Dict[str, List[str]]:
    """Return ``{name → argv}`` for declared checks. Empty dict when
    the config is missing / malformed / has no ``checks`` block.

    This legacy view intentionally returns only the base command for
    each check. Use ``load_check_specs`` when argument policy matters.
    """
    return {name: list(spec["cmd"]) for name, spec in load_check_specs(project_root).items()}


def load_check_specs(project_root: str) -> Dict[str, CheckSpec]:
    """Return normalised check specs keyed by check name.

    Backwards-compatible list entries become ``{"cmd": cmd,
    "args_policy": {}}``. Object entries must include ``cmd`` and may
    include an ``args_policy`` object.
    """
    data = _load_config(project_root)
    raw = data.get("checks")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, CheckSpec] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name:
            continue
        if _valid_argv(spec):
            out[name] = {"cmd": list(cast(List[str], spec)), "args_policy": {}}
            continue
        if not isinstance(spec, dict):
            continue
        cmd = spec.get("cmd")
        if not _valid_argv(cmd):
            continue
        cmd_tokens = cast(List[str], cmd)
        policy = spec.get("args_policy", {})
        if not isinstance(policy, dict):
            policy = {}
        out[name] = {"cmd": list(cmd_tokens), "args_policy": dict(policy)}
    return out


def list_checks(project_root: str) -> List[str]:
    """Sorted list of available check names."""
    return sorted(load_check_specs(project_root).keys())


def _normalise_extra_args(args: Optional[List[str]]) -> List[str]:
    if args is None:
        return []
    if not isinstance(args, list) or not all(isinstance(a, str) and a for a in args):
        raise ValueError("args must be a list of non-empty strings")
    return list(args)


def _is_safe_relative_path(project_root: str, roots: List[str], arg: str) -> bool:
    if os.path.isabs(arg):
        return False
    norm = os.path.normpath(arg)
    if norm == "." or norm.startswith("..") or os.path.isabs(norm):
        return False
    abs_path = os.path.abspath(os.path.join(project_root, norm))
    root_abs = os.path.abspath(project_root)
    try:
        common = os.path.commonpath([root_abs, abs_path])
    except ValueError:
        return False
    if common != root_abs:
        return False
    if not roots:
        return True
    for root in roots:
        if not isinstance(root, str) or not root:
            continue
        root_norm = os.path.normpath(root)
        if root_norm == ".":
            return True
        if norm == root_norm or norm.startswith(root_norm + os.sep):
            return True
    return False


def _flag_name(arg: str) -> str:
    return arg.split("=", 1)[0]


def _validate_extra_args(
    project_root: str,
    policy: Dict[str, Any],
    args: List[str],
) -> Tuple[bool, str]:
    """Validate caller-supplied argv suffix against an args policy."""
    if not args:
        return True, ""
    if not policy:
        return False, "check does not allow extra args"

    allow_flags = {str(v) for v in policy.get("allow_flags", []) if isinstance(v, str)}
    allow_flag_values = {str(v) for v in policy.get("allow_flag_values", []) if isinstance(v, str)}
    deny_flags = {str(v) for v in policy.get("deny_flags", []) if isinstance(v, str)}
    allow_paths = bool(policy.get("allow_paths", False))
    path_roots = [str(v) for v in policy.get("path_roots", []) if isinstance(v, str) and v.strip()]

    expecting_value_for: Optional[str] = None
    for arg in args:
        if expecting_value_for is not None:
            if arg.startswith("-"):
                return False, f"flag '{expecting_value_for}' requires a value"
            expecting_value_for = None
            continue

        if arg.startswith("-"):
            flag = _flag_name(arg)
            if flag in deny_flags:
                return False, f"flag '{flag}' is denied"
            if "=" in arg:
                if flag not in allow_flag_values:
                    return False, f"flag '{flag}' is not allowed"
                continue
            if flag in allow_flag_values:
                expecting_value_for = flag
                continue
            if flag in allow_flags:
                continue
            return False, f"flag '{flag}' is not allowed"

        if allow_paths and _is_safe_relative_path(project_root, path_roots, arg):
            continue
        return False, f"arg '{arg}' is not allowed"

    if expecting_value_for is not None:
        return False, f"flag '{expecting_value_for}' requires a value"
    return True, ""


def _tail(text: str, n: int) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= n:
        return text.rstrip("\n")
    return "\n".join(lines[-n:])


def _summarise_pytest(stdout: str, stderr: str) -> Optional[str]:
    """Extract a one-line pytest-style summary if recognisable.

    Examples:
      ``95 passed in 7.5s``
      ``5 failed, 90 passed in 12.3s``
      ``2 errors``
    """
    import re

    pat = re.compile(
        r"(?P<line>\d+\s+(?:passed|failed|error|errors|skipped|deselected)"
        r"(?:[,\s\d\w]*?)\s+in\s+\d+\.?\d*\s*s)"
    )
    for source in (stdout, stderr):
        for line in reversed((source or "").splitlines()):
            match = pat.search(line)
            if match:
                return match.group("line")
    return None


def run_check(
    project_root: str,
    name: str,
    timeout_sec: Optional[int] = None,
    args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute a whitelisted check.

    Returns a dict with keys:
      * ``name`` — echoed input
      * ``command`` — the argv that ran (or empty list when refused)
      * ``returncode`` — process exit code (-1 on timeout, -2 on
        unknown name, -3 on spawn failure)
      * ``duration_ms``
      * ``stdout_tail`` / ``stderr_tail`` — last 50 lines of each
      * ``summary`` — best-effort pytest-style summary line, or None
      * ``error`` — short reason on returncode<0; absent otherwise
    """
    checks = load_check_specs(project_root)
    if name not in checks:
        return {
            "name": name,
            "command": [],
            "returncode": -2,
            "duration_ms": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "summary": None,
            "error": f"unknown check '{name}' (available: {sorted(checks)})",
        }
    extra_args: List[str] = []
    try:
        extra_args = _normalise_extra_args(args)
    except ValueError as exc:
        return {
            "name": name,
            "command": [],
            "returncode": -4,
            "duration_ms": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "summary": None,
            "error": str(exc),
        }

    spec = checks[name]
    allowed, reason = _validate_extra_args(project_root, spec.get("args_policy", {}), extra_args)
    if not allowed:
        return {
            "name": name,
            "command": list(spec["cmd"]),
            "returncode": -4,
            "duration_ms": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "summary": None,
            "error": f"extra args refused: {reason}",
        }

    cmd = list(spec["cmd"]) + extra_args
    timeout = timeout_sec or _DEFAULT_TIMEOUT_SEC
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout_tail = _tail(proc.stdout or "", _TAIL_LINES)
        stderr_tail = _tail(proc.stderr or "", _TAIL_LINES)
        return {
            "name": name,
            "command": cmd,
            "returncode": proc.returncode,
            "duration_ms": duration_ms,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "summary": _summarise_pytest(stdout_tail, stderr_tail),
        }
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "name": name,
            "command": cmd,
            "returncode": -1,
            "duration_ms": duration_ms,
            "stdout_tail": _tail(exc.stdout or "", _TAIL_LINES)
            if isinstance(exc.stdout, str)
            else "",
            "stderr_tail": _tail(exc.stderr or "", _TAIL_LINES)
            if isinstance(exc.stderr, str)
            else "",
            "summary": None,
            "error": f"timeout after {timeout}s",
        }
    except (OSError, FileNotFoundError) as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "name": name,
            "command": cmd,
            "returncode": -3,
            "duration_ms": duration_ms,
            "stdout_tail": "",
            "stderr_tail": "",
            "summary": None,
            "error": f"spawn failed: {exc}",
        }
