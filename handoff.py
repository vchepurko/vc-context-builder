"""Project-local handoff memory for subscription-chat agent switches.

The handoff file is deliberately plain Markdown so any IDE agent can read it
without needing vc-context tooling. The CLI only creates, snapshots, and renders
prompts around that file.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

HANDOFF_POINTER = "HANDOFF.md"
HANDOFF_PATH = ".vc-context/HANDOFF.md"
HANDOFF_LOG_DIR = ".vc-context/handoffs"


@dataclass(frozen=True)
class HandoffPaths:
    root: Path
    pointer: Path
    memory: Path


def paths(project_root: str | os.PathLike[str]) -> HandoffPaths:
    root = Path(project_root).resolve()
    return HandoffPaths(root=root, pointer=root / HANDOFF_POINTER, memory=root / HANDOFF_PATH)


def init_handoff(
    project_root: str | os.PathLike[str],
    *,
    task: str = "",
    agent: str = "",
    force: bool = False,
) -> dict[str, Any]:
    hp = paths(project_root)
    hp.memory.parent.mkdir(parents=True, exist_ok=True)
    (hp.root / HANDOFF_LOG_DIR).mkdir(parents=True, exist_ok=True)

    pointer_written = _write_if_needed(hp.pointer, _pointer_text(), force=force)
    memory_written = _write_if_needed(
        hp.memory,
        render_handoff(
            hp.root,
            task=task,
            agent=agent,
            status="in_progress" if task else "unknown",
            next_step="Fill this before handing the task to another agent.",
            notes=["Initial handoff file created."],
            blockers=[],
        ),
        force=force,
    )
    return {
        "pointer": str(hp.pointer),
        "memory": str(hp.memory),
        "pointer_written": pointer_written,
        "memory_written": memory_written,
    }


def snapshot_handoff(
    project_root: str | os.PathLike[str],
    *,
    task: str = "",
    agent: str = "",
    status: str = "in_progress",
    next_step: str = "",
    notes: list[str] | None = None,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    hp = paths(project_root)
    hp.memory.parent.mkdir(parents=True, exist_ok=True)
    log_dir = hp.root / HANDOFF_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    pointer_written = _write_if_needed(hp.pointer, _pointer_text(), force=False)
    content = render_handoff(
        hp.root,
        task=task,
        agent=agent,
        status=status,
        next_step=next_step,
        notes=notes or [],
        blockers=blockers or [],
    )
    hp.memory.write_text(content, encoding="utf-8")
    log_path = log_dir / _log_filename(task=task, agent=agent)
    log_path.write_text(content, encoding="utf-8")
    return {
        "pointer": str(hp.pointer),
        "memory": str(hp.memory),
        "log": str(log_path),
        "pointer_written": pointer_written,
        "bytes": len(content.encode("utf-8")),
    }


def status_handoff(project_root: str | os.PathLike[str]) -> dict[str, Any]:
    hp = paths(project_root)
    exists = hp.memory.exists()
    content = hp.memory.read_text(encoding="utf-8") if exists else ""
    return {
        "pointer": str(hp.pointer),
        "memory": str(hp.memory),
        "pointer_exists": hp.pointer.exists(),
        "memory_exists": exists,
        "task": _extract_field(content, "Task"),
        "status": _extract_field(content, "Status"),
        "agent": _extract_field(content, "Agent"),
        "next_step": _extract_section(content, "Next Step"),
        "latest_log": _latest_log(hp.root),
    }


def prompt_handoff(project_root: str | os.PathLike[str], *, agent: str = "") -> str:
    target = agent or "the next agent"
    return (
        f"You are {target} continuing work in this repository.\n\n"
        f"Read `{HANDOFF_POINTER}` first. It points to `{HANDOFF_PATH}`, the current "
        "project handoff memory.\n\n"
        "Rules:\n"
        "1. Continue from `Next Step`; do not restart completed analysis.\n"
        "2. Respect `Do Not Touch` and blockers.\n"
        f"3. If the current file is unclear, inspect `{HANDOFF_LOG_DIR}/` for "
        "recent snapshots.\n"
        "4. Before stopping with unfinished work, update the handoff.\n"
        "5. In your final response, report what changed, verification, blockers, "
        "and the next handoff step.\n"
    )


def render_handoff(
    project_root: Path,
    *,
    task: str,
    agent: str,
    status: str,
    next_step: str,
    notes: list[str],
    blockers: list[str],
) -> str:
    git = _git_snapshot(project_root)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    task_text = task or "unknown"
    agent_text = agent or "unknown"
    next_text = next_step or "Decide the next concrete step before switching agents."
    notes_text = _bullets(notes) if notes else "- No manual notes recorded."
    blockers_text = _bullets(blockers) if blockers else "- None recorded."
    status_short = git["status_short"] or "(clean)"
    diff_stat = git["diff_stat"] or "(no diff)"
    branch = git["branch"] or "unknown"
    return f"""# HANDOFF

Last updated: {now}
Task: {task_text}
Status: {status}
Agent: {agent_text}
Branch: {branch}
Worktree: {project_root}

## Purpose

This file is project-local handoff memory. Any Codex, Claude, Gemini, Cursor,
Aider, or other coding agent should read it before continuing an in-progress
task in this repository.

Every `vc-context handoff snapshot` also writes an immutable-ish copy under
`{HANDOFF_LOG_DIR}/` so handoffs can be audited if the current file gets
overwritten.

## Current State

{notes_text}

## Git Snapshot

```text
{status_short}
```

## Diff Stat

```text
{diff_stat}
```

## Blockers

{blockers_text}

## Next Step

{next_text}

## Do Not Touch

- Do not commit or push directly to `main`.
- Do not revert unrelated user changes.
- Follow repo-local `AGENTS.md` rules before editing.

## Resume Prompt

Read this HANDOFF and continue from `Next Step`. Do not redo completed work.
Before stopping with unfinished work, update this file with the new state.
"""


def _write_if_needed(path: Path, content: str, *, force: bool) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _pointer_text() -> str:
    return f"""# HANDOFF

Current project handoff memory lives in:

`{HANDOFF_PATH}`

Every coding agent working in this repository should read that file before
continuing an in-progress task, especially after chat/model/account switches.

Recent snapshots are archived in `{HANDOFF_LOG_DIR}/`.
"""


def _git_snapshot(project_root: Path) -> dict[str, str]:
    return {
        "branch": _run_git(project_root, ["branch", "--show-current"]),
        "status_short": _run_git(project_root, ["status", "--short", "--branch"]),
        "diff_stat": _run_git(project_root, ["diff", "--stat"]),
    }


def _run_git(project_root: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(git unavailable: {exc})"
    out = proc.stdout.strip()
    err = proc.stderr.strip()
    if proc.returncode != 0:
        return err or f"(git exited {proc.returncode})"
    return out


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if item.strip()) or "- None recorded."


def _log_filename(*, task: str, agent: str) -> str:
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    label = _slug(agent or task or "handoff")
    return f"{stamp}-{label}.md"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip().lower()).strip("-._")
    return slug[:48] or "handoff"


def _latest_log(project_root: Path) -> str:
    log_dir = project_root / HANDOFF_LOG_DIR
    if not log_dir.exists():
        return ""
    logs = sorted(log_dir.glob("*.md"), key=lambda path: path.name)
    return str(logs[-1]) if logs else ""


def _extract_field(content: str, field: str) -> str:
    prefix = f"{field}:"
    for line in content.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _extract_section(content: str, heading: str) -> str:
    lines = content.splitlines()
    marker = f"## {heading}"
    for i, line in enumerate(lines):
        if line.strip() != marker:
            continue
        body: list[str] = []
        for next_line in lines[i + 1 :]:
            if next_line.startswith("## "):
                break
            body.append(next_line)
        return "\n".join(body).strip()
    return ""
