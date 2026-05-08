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

Each value is a **list of argv tokens** — no shell, no string
splitting, no injection. The runner executes the command with
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
from typing import Any, Dict, List, Optional

CONFIG_RELATIVE_PATH = os.path.join(".vc-context", "conventions.json")

_TAIL_LINES = 50  # max lines kept from each of stdout / stderr
_DEFAULT_TIMEOUT_SEC = 300


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


def load_checks(project_root: str) -> Dict[str, List[str]]:
    """Return ``{name → argv}`` for declared checks. Empty dict when
    the config is missing / malformed / has no ``checks`` block.
    """
    data = _load_config(project_root)
    raw = data.get("checks")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for name, cmd in raw.items():
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(cmd, list) or not cmd:
            continue
        if not all(isinstance(tok, str) and tok for tok in cmd):
            continue
        out[name] = list(cmd)
    return out


def list_checks(project_root: str) -> List[str]:
    """Sorted list of available check names."""
    return sorted(load_checks(project_root).keys())


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
    checks = load_checks(project_root)
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
    cmd = checks[name]
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
