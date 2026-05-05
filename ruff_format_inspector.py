"""Ruff formatter inspector — structured view of ``ruff format --check``.

Sibling to ``ruff_inspector`` but for the *formatter* (whitespace,
quote style, line breaks) rather than the linter. Symmetric MCP
surface so an agent can ask "is the codebase formatted?" without
the model context absorbing the full text output.

Output shape::

    {
        "total": int,                  # files needing reformat
        "files": ["path/a.py", ...]    # absent when summary=True
    }

``summary=True`` returns just ``{"total": N}`` — the absolute minimum
token footprint (~12 bytes for "all clean").

Exit code semantics: ``ruff format --check`` exits 1 when files need
reformatting, 0 when all clean. We always parse stdout regardless of
return code; spawn / decode failures fall back to an empty list.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional


DEFAULT_COMMAND = ["uv", "run", "ruff", "format", "--check", "."]

# Matches a "Would reformat: <path>" line. ruff prints these on stdout
# (not stderr), one per file.
_REFORMAT_RE = re.compile(r"^Would reformat:\s+(.+)$")


def _load_command(project_root: str) -> List[str]:
    """``conventions.json["ruff_format"]["command"]`` overrides the
    default ``uv run ruff format --check .``. Same convention as the
    ``ruff_inspector`` override key, just under its own block."""
    conv_path = os.path.join(project_root, ".vc-context", "conventions.json")
    if os.path.isfile(conv_path):
        try:
            with open(conv_path, "r", encoding="utf-8") as fh:
                conv = json.load(fh)
            override = (
                conv.get("ruff_format", {}).get("command")
                if isinstance(conv, dict) else None
            )
            if isinstance(override, list) and all(isinstance(x, str) for x in override):
                return list(override)
        except (OSError, json.JSONDecodeError):
            pass
    return list(DEFAULT_COMMAND)


def run_ruff_format(
    project_root: str, command: Optional[List[str]] = None,
) -> List[str]:
    """Execute ruff format --check and return the list of files that
    would be reformatted, project-relative. Empty list when ruff is
    clean OR when invocation failed (we don't distinguish — caller
    treats empty as "nothing to do")."""
    cmd = command or _load_command(project_root)
    try:
        result = subprocess.run(
            cmd, cwd=project_root, capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    files: List[str] = []
    for line in result.stdout.splitlines():
        m = _REFORMAT_RE.match(line.strip())
        if m:
            files.append(_norm_file(m.group(1).strip(), project_root))
    return files


def _norm_file(path: str, project_root: str) -> str:
    """Strip the project root + leading slash so output stays
    project-relative regardless of where ruff was invoked from."""
    abs_root = os.path.abspath(project_root) + os.sep
    if path.startswith(abs_root):
        return path[len(abs_root):].replace(os.sep, "/")
    return path.replace(os.sep, "/")


def collect(
    project_root: str,
    *,
    path_prefix: Optional[str] = None,
    summary: bool = False,
    limit: int = 50,
    command: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run the formatter check, filter, summarise.

    ``path_prefix`` is a project-relative startswith filter
    (``"services/notify"`` matches every file in that tree).
    ``summary=True`` drops the file list — minimum-token signal.
    """
    files = run_ruff_format(project_root, command=command)
    if path_prefix:
        files = [f for f in files if f.startswith(path_prefix)]
    files.sort()
    out: Dict[str, Any] = {"total": len(files)}
    if not summary:
        out["files"] = files[:limit] if limit else files
    return out
