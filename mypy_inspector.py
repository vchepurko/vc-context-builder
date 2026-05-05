"""Mypy violations inspector — structured JSON view of ``mypy``.

Wraps ``mypy --output=json .`` so an MCP client can ask "how is this
codebase doing on type-check?" without dumping the full text output
into the model context.

Output shape::

    {
        "total":   int,
        "by_code": {"union-attr": 312, "assignment": 47, ...},
        "by_file": {"bot/handlers/admin_catalog.py": 30, ...},
        "violations": [
            {"file": "...", "line": 12, "end_line": 12,
             "code": "union-attr", "severity": "error", "message": "..."},
            ...                                      // up to `limit`
        ],
    }

``summary=True`` drops the ``violations`` list — useful as the first
call to triage scope, then drill in with ``code`` / ``path_prefix``
filters. Symmetric with ``ruff_inspector``.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List, Optional


DEFAULT_COMMAND = ["uv", "run", "mypy", "--output=json", "--no-color-output", "."]

# Project markers that imply mypy is meaningful to run. Mirrors the
# ruff inspector's gate so a frontend-only repo gets a clean
# ``{skipped: true}`` instead of a noisy mypy invocation failure.
_PYTHON_MARKER_FILES = ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "requirements.txt")

# A configured-mypy marker — at least one of these must exist for us
# to run mypy. ``[tool.mypy]`` in pyproject.toml or a top-level mypy
# config file. Without configuration the tool reports thousands of
# strict-defaults errors that aren't actionable.
_MYPY_CONFIG_FILES = ("mypy.ini", ".mypy.ini")


def _has_tool_mypy_in_pyproject(project_root: str) -> bool:
    """Cheap text scan for ``[tool.mypy]`` in pyproject.toml. We don't
    parse TOML to keep this stdlib-only — false positives (the literal
    string in a comment) are harmless: they just stop the skip."""
    pp = os.path.join(project_root, "pyproject.toml")
    if not os.path.isfile(pp):
        return False
    try:
        with open(pp, "r", encoding="utf-8") as fh:
            return "[tool.mypy]" in fh.read()
    except OSError:
        return False


def should_skip_mypy(project_root: str) -> tuple[bool, str]:
    """Return ``(skip, reason)`` — whether to bypass mypy for this
    project. Skips when:

    1. ``.vc-context/conventions.json`` has ``"mypy": {"enabled":
       false}`` (explicit team opt-out, e.g. a polyglot repo where
       mypy lives only in CI and shouldn't bleed into MCP responses).
    2. Auto-detect found no Python project markers at the root and no
       ``*.py`` file at depth 1. Frontend-only projects should not
       run mypy.
    3. Auto-detect found no mypy config (``[tool.mypy]`` in
       pyproject.toml, ``mypy.ini``, ``.mypy.ini``). Without config,
       mypy's strict defaults emit noise that isn't actionable.

    An explicit ``"mypy": {"enabled": true}`` in conventions.json
    forces the tool on even when detection would have skipped — useful
    for projects where the Python code lives deeper than depth 1 or
    config is non-standard.
    """
    conv_path = os.path.join(project_root, ".vc-context", "conventions.json")
    if os.path.isfile(conv_path):
        try:
            with open(conv_path, "r", encoding="utf-8") as fh:
                conv = json.load(fh)
            mypy_cfg = conv.get("mypy") if isinstance(conv, dict) else None
            if isinstance(mypy_cfg, dict):
                enabled = mypy_cfg.get("enabled")
                if enabled is False:
                    return True, "disabled in .vc-context/conventions.json"
                if enabled is True:
                    return False, ""
        except (OSError, json.JSONDecodeError):
            pass

    # (2) — Python project at all?
    has_python_marker = any(
        os.path.isfile(os.path.join(project_root, m)) for m in _PYTHON_MARKER_FILES
    )
    if not has_python_marker:
        try:
            for entry in os.listdir(project_root):
                if entry.endswith(".py"):
                    has_python_marker = True
                    break
        except OSError:
            return True, "project root not readable"
    if not has_python_marker:
        return True, "no Python project (no pyproject.toml / setup.py / *.py at root)"

    # (3) — mypy specifically configured?
    has_config = (
        _has_tool_mypy_in_pyproject(project_root)
        or any(os.path.isfile(os.path.join(project_root, m)) for m in _MYPY_CONFIG_FILES)
    )
    if not has_config:
        return True, "no mypy config ([tool.mypy] in pyproject.toml or mypy.ini)"
    return False, ""


def _load_command(project_root: str) -> List[str]:
    """``conventions.json["mypy"]["command"]`` overrides the default
    ``uv run mypy --output=json --no-color-output .``."""
    conv_path = os.path.join(project_root, ".vc-context", "conventions.json")
    if os.path.isfile(conv_path):
        try:
            with open(conv_path, "r", encoding="utf-8") as fh:
                conv = json.load(fh)
            override = (
                conv.get("mypy", {}).get("command")
                if isinstance(conv, dict) else None
            )
            if isinstance(override, list) and all(isinstance(x, str) for x in override):
                return list(override)
        except (OSError, json.JSONDecodeError):
            pass
    return list(DEFAULT_COMMAND)


def run_mypy(
    project_root: str, command: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Execute mypy in JSON-line mode and return parsed records. Each
    line of stdout is one JSON object; we skip lines that aren't JSON
    (the trailing ``Found N errors`` summary, mypy errors, the uv
    VIRTUAL_ENV warning). ``mypy`` exits non-zero when type errors
    exist — we treat that as success and parse stdout, only treating
    spawn / decode failures as empty."""
    cmd = command or _load_command(project_root)
    try:
        result = subprocess.run(
            cmd, cwd=project_root, capture_output=True, text=True, timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    out: List[Dict[str, Any]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _norm_file(path: str, project_root: str) -> str:
    """Strip the project root + leading slash so output stays
    project-relative regardless of where mypy was invoked from."""
    abs_root = os.path.abspath(project_root) + os.sep
    if path.startswith(abs_root):
        return path[len(abs_root):].replace(os.sep, "/")
    return path.replace(os.sep, "/")


def _to_entry(v: Dict[str, Any], project_root: str) -> Dict[str, Any]:
    """Compress a mypy record into the fields the MCP client actually
    needs. Drops column / end_column / hint to keep the response lean
    — file:line + code + message is enough for triage."""
    return {
        "file": _norm_file(v.get("file", ""), project_root),
        "line": int(v.get("line", 0) or 0),
        "end_line": int(v.get("end_line", 0) or 0),
        "code": v.get("code") or "",
        "severity": v.get("severity") or "",
        "message": v.get("message") or "",
    }


def collect(
    project_root: str,
    *,
    code: Optional[str] = None,
    path_prefix: Optional[str] = None,
    severity: Optional[str] = None,
    summary: bool = False,
    limit: int = 50,
    command: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run mypy, filter, summarise. Filters AND-combine.

    ``code`` matches the violation's rule code exactly (e.g.
    ``"union-attr"``). ``path_prefix`` is a project-relative
    startswith match. ``severity`` filters to one of ``error``,
    ``note``, ``warning``.

    Returns ``{total: 0, skipped: true, reason: "..."}`` instead when
    the project isn't Python (auto-detected), has no mypy config, or
    has explicitly opted out via
    ``conventions.json["mypy"]["enabled"] = false``.
    """
    skip, reason = should_skip_mypy(project_root)
    if skip:
        return {"total": 0, "by_code": {}, "by_file": {}, "skipped": True, "reason": reason}
    raw = run_mypy(project_root, command=command)
    entries = [_to_entry(v, project_root) for v in raw]

    if code:
        entries = [e for e in entries if e["code"] == code]
    if path_prefix:
        entries = [e for e in entries if e["file"].startswith(path_prefix)]
    if severity:
        entries = [e for e in entries if e["severity"] == severity]

    by_code: Dict[str, int] = {}
    by_file: Dict[str, int] = {}
    for e in entries:
        by_code[e["code"]] = by_code.get(e["code"], 0) + 1
        by_file[e["file"]] = by_file.get(e["file"], 0) + 1

    out: Dict[str, Any] = {
        "total": len(entries),
        "by_code": dict(sorted(by_code.items(), key=lambda x: (-x[1], x[0]))),
        "by_file": dict(sorted(by_file.items(), key=lambda x: (-x[1], x[0]))),
    }
    if not summary:
        out["violations"] = entries[:limit] if limit else entries
    return out
