"""Agent entrypoint helpers for shared handoff sessions."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from handoff import prompt_handoff, status_handoff
from status import get_status

DEFAULT_STALE_SECONDS = 30 * 60

_HERE = os.path.dirname(os.path.abspath(__file__))


def maybe_reindex(
    project_root: str,
    *,
    mode: str = "auto",
    stale_seconds: int = DEFAULT_STALE_SECONDS,
) -> dict[str, Any]:
    """Rebuild the project index according to an agent-session policy."""
    root = os.path.abspath(project_root)
    status = get_status(root)
    index = status.get("index", {})
    should_run = mode == "always" or (
        mode == "auto" and _agent_index_stale(index, stale_seconds=stale_seconds)
    )
    if mode == "never" or not should_run:
        return {
            "ok": True,
            "ran": False,
            "reason": "disabled" if mode == "never" else "fresh",
            "stale_seconds": stale_seconds,
        }

    builder = os.path.join(_HERE, "agent_map.py")
    if not os.path.isfile(builder):
        return {"ok": False, "ran": False, "error": f"agent_map.py not found at {builder}"}

    proc = subprocess.run(
        [sys.executable, builder, "--root", root],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return {
        "ok": proc.returncode == 0,
        "ran": True,
        "returncode": proc.returncode,
        "stale_seconds": stale_seconds,
        "stderr_tail": proc.stderr[-1000:],
    }


def agent_start(
    project_root: str,
    *,
    agent: str = "",
    reindex: str = "auto",
    stale_seconds: int = DEFAULT_STALE_SECONDS,
) -> dict[str, Any]:
    """Return the shared-session startup packet for a coding agent."""
    root = os.path.abspath(project_root)
    before = get_status(root)
    reindex_result = maybe_reindex(root, mode=reindex, stale_seconds=stale_seconds)
    after = get_status(root) if reindex_result.get("ran") else before
    handoff = status_handoff(root)
    return {
        "agent": agent,
        "project_root": root,
        "rules": _rules_files(root),
        "handoff": handoff,
        "index": after.get("index", {}),
        "reindex": reindex_result,
        "prompt": startup_prompt(
            root,
            agent=agent,
            handoff=handoff,
            index=after.get("index", {}),
            reindex=reindex_result,
        ),
    }


def startup_prompt(
    project_root: str,
    *,
    agent: str = "",
    handoff: dict[str, Any] | None = None,
    index: dict[str, Any] | None = None,
    reindex: dict[str, Any] | None = None,
) -> str:
    """Render concise instructions that can be pasted into any agent chat."""
    root = os.path.abspath(project_root)
    handoff = handoff or status_handoff(root)
    index = index or get_status(root).get("index", {})
    reindex = reindex or {"ran": False}
    rules = _rules_files(root)
    lines = [
        prompt_handoff(root, agent=agent).rstrip(),
        "",
        "Agent-start checklist:",
        f"- Project root: `{root}`",
        f"- Rules files: {', '.join(f'`{path}`' for path in rules) or '`AGENTS.md` not found'}",
        f"- Current handoff exists: {handoff.get('memory_exists')}",
        f"- Index exists: {index.get('exists')}  age_seconds={index.get('age_seconds')}",
        f"- Reindex ran: {reindex.get('ran')}  ok={reindex.get('ok')}",
        "",
        "Start by reading the rules files and current handoff, then continue from `Next Step`.",
    ]
    return "\n".join(lines) + "\n"


def _agent_index_stale(index: dict[str, Any], *, stale_seconds: int) -> bool:
    if index.get("error"):
        return True
    if not index.get("exists"):
        return True
    age = index.get("age_seconds")
    if age is None:
        return True
    try:
        return float(age) >= stale_seconds
    except (TypeError, ValueError):
        return True


def _rules_files(project_root: str) -> list[str]:
    out: list[str] = []
    for name in ("AGENTS.md", "CLAUDE.md", "HANDOFF.md"):
        path = os.path.join(project_root, name)
        if os.path.exists(path):
            out.append(path)
    return out
