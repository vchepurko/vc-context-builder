"""Ruff violations inspector — structured JSON view of ``ruff check``.

Wraps ``ruff check . --output-format=json`` so an MCP client can ask
"how is this codebase doing?" without dumping the full text output
into the model context.

Output shape::

    {
        "total":   int,
        "by_code": {"UP006": 24, "UP045": 17, ...},
        "by_file": {"services/notify/audience.py": 17, ...},
        "violations": [
            {"file": "...", "line": 12, "end_line": 12,
             "code": "UP006", "message": "..."},
            ...                                      // up to `limit`
        ],
    }

``summary=True`` drops the ``violations`` list — useful as the first
call to triage scope, then drill in with ``code`` / ``path_prefix``
filters.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List, Optional


DEFAULT_COMMAND = ["uv", "run", "ruff", "check", "--output-format=json", "."]


def _load_command(project_root: str) -> List[str]:
    """``conventions.json["ruff"]["command"]`` overrides the default
    ``uv run ruff check --output-format=json .``."""
    conv_path = os.path.join(project_root, ".vc-context", "conventions.json")
    if os.path.isfile(conv_path):
        try:
            with open(conv_path, "r", encoding="utf-8") as fh:
                conv = json.load(fh)
            override = (
                conv.get("ruff", {}).get("command")
                if isinstance(conv, dict) else None
            )
            if isinstance(override, list) and all(isinstance(x, str) for x in override):
                return list(override)
        except (OSError, json.JSONDecodeError):
            pass
    return list(DEFAULT_COMMAND)


def run_ruff(
    project_root: str, command: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Execute ruff and return the parsed JSON list. Empty list when
    ruff isn't installed or returns non-JSON. ``ruff check`` exits
    non-zero when violations exist — we treat that as success and
    parse stdout, only treating spawn / decode failures as empty."""
    cmd = command or _load_command(project_root)
    try:
        result = subprocess.run(
            cmd, cwd=project_root, capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _norm_file(path: str, project_root: str) -> str:
    """Strip the project root + leading slash so output stays
    project-relative regardless of where ruff was invoked from."""
    abs_root = os.path.abspath(project_root) + os.sep
    if path.startswith(abs_root):
        return path[len(abs_root):].replace(os.sep, "/")
    return path.replace(os.sep, "/")


def _to_entry(v: Dict[str, Any], project_root: str) -> Dict[str, Any]:
    """Compress a ruff record into the four fields the MCP client
    actually needs. Strips fix suggestions and end-column noise that
    would balloon the response without helping triage."""
    location = v.get("location") or {}
    end = v.get("end_location") or {}
    return {
        "file": _norm_file(v.get("filename", ""), project_root),
        "line": int(location.get("row", 0)),
        "end_line": int(end.get("row", 0)),
        "code": v.get("code", ""),
        "message": v.get("message", ""),
    }


def collect(
    project_root: str,
    *,
    code: Optional[str] = None,
    path_prefix: Optional[str] = None,
    summary: bool = False,
    limit: int = 50,
    command: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run ruff, filter, summarise. Filters AND-combine.

    ``code`` matches the violation's rule code exactly (e.g.
    ``"UP006"``). ``path_prefix`` is a project-relative startswith
    match (``"services/notify"`` matches every file in that tree).
    """
    raw = run_ruff(project_root, command=command)
    entries = [_to_entry(v, project_root) for v in raw]

    # Filter.
    if code:
        entries = [e for e in entries if e["code"] == code]
    if path_prefix:
        entries = [e for e in entries if e["file"].startswith(path_prefix)]

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
