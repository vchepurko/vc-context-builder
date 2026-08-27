"""Named, file-backed semaphores for shared resources multiple agents can race on
(a local docker-compose deploy slot, a shared DB migration, anything with exactly
one legitimate concurrent holder in this project's worktree).

Companion to ``handoff.py``: that module is a continuity NOTE (single writer,
last-write-wins, meant for chat/agent switches on one task). This module is a
MUTUAL-EXCLUSION primitive (many agents, first-claim-wins, meant to prevent two
agents from doing the same exclusive operation at once — e.g. two sessions both
running ``docker compose build --force-recreate`` against the same shared engine
containers, corrupting the Docker daemon's container-name index).

Atomicity: claiming a lock is a single ``os.open(path, O_CREAT | O_EXCL)`` call —
the OS either creates the file or raises FileExistsError, never both agents
succeeding. This is the check-then-act race this project's own CLAUDE.md warns
against, solved the same way the DB conventions solve it (push the decision into
an atomic primitive, never read-then-write from application code).
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

LOCKS_DIR = ".vc-context/locks"
LOCKS_LOG_DIR = ".vc-context/locks/history"


@dataclass(frozen=True)
class LockPaths:
    root: Path
    dir: Path
    history: Path

    def lock_file(self, name: str) -> Path:
        return self.dir / f"{_slug(name)}.lock"


def paths(project_root: str | os.PathLike[str]) -> LockPaths:
    root = Path(project_root).resolve()
    return LockPaths(root=root, dir=root / LOCKS_DIR, history=root / LOCKS_LOG_DIR)


def acquire(
    project_root: str | os.PathLike[str],
    name: str,
    *,
    agent: str,
    task: str = "",
) -> dict[str, Any]:
    """Try to claim ``name``. Atomic: either this call creates the lock file (and
    nobody else can), or it fails and returns the CURRENT holder's own info so the
    caller can see who to wait on or break — never a silent guess."""
    if not agent:
        raise ValueError("acquire() requires a real agent identity — an anonymous "
                          "lock is useless to whoever finds it held")
    lp = paths(project_root)
    lp.dir.mkdir(parents=True, exist_ok=True)
    lock_path = lp.lock_file(name)
    record = _record(name=name, agent=agent, task=task)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        held_by = _read_record(lock_path)
        return {"acquired": False, "name": name, "lock": str(lock_path), "held_by": held_by}
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(record, indent=2))
    return {"acquired": True, "name": name, "lock": str(lock_path), "held_by": record}


def release(
    project_root: str | os.PathLike[str],
    name: str,
    *,
    agent: str,
) -> dict[str, Any]:
    """Release a lock this ``agent`` itself holds. Refuses (no-op, ``released:
    False``) if the lock is absent or held by someone else — a blind unlink would
    let agent B silently drop agent A's still-active lock. Callers that genuinely
    need to clear another agent's lock must use ``force_break`` and say why."""
    lp = paths(project_root)
    lock_path = lp.lock_file(name)
    held_by = _read_record(lock_path)
    if held_by is None:
        return {"released": False, "reason": "not_locked", "name": name}
    if held_by.get("agent") != agent:
        return {"released": False, "reason": "held_by_other", "name": name, "held_by": held_by}
    _archive(project_root, name, held_by, event="release", by=agent, reason="")
    lock_path.unlink(missing_ok=True)
    return {"released": True, "name": name, "held_by": held_by}


def force_break(
    project_root: str | os.PathLike[str],
    name: str,
    *,
    agent: str,
    reason: str,
) -> dict[str, Any]:
    """Forcibly clear ``name``'s lock regardless of who holds it — the explicit
    interrupt path for a stale lock (crashed session, dead PID) that would
    otherwise block every other agent forever. Always logs who broke it and why
    to ``locks/history/`` so it is auditable, not a silent override."""
    if not reason:
        raise ValueError("force_break() requires a reason — this is a deliberate "
                          "override of someone else's claim, not a routine release")
    lp = paths(project_root)
    lock_path = lp.lock_file(name)
    held_by = _read_record(lock_path)
    _archive(project_root, name, held_by, event="force_break", by=agent, reason=reason)
    lock_path.unlink(missing_ok=True)
    return {"broken": True, "name": name, "was_held_by": held_by, "broken_by": agent, "reason": reason}


def status(project_root: str | os.PathLike[str], name: str) -> dict[str, Any]:
    """Current state of one named lock: free, or held-by with an age hint (a lock
    held for a suspiciously long time likely means a crashed session, not real
    work still in progress — surfaced as a HINT, never auto-broken, since a
    genuinely long build/migration must not be silently interrupted)."""
    lp = paths(project_root)
    held_by = _read_record(lp.lock_file(name))
    if held_by is None:
        return {"name": name, "held": False}
    age_seconds = _age_seconds(held_by)
    return {
        "name": name,
        "held": True,
        "held_by": held_by,
        "age_seconds": age_seconds,
        "possibly_stale": age_seconds is not None and age_seconds > 3600,
    }


def list_locks(project_root: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Every currently held lock in this project — what ``## Active Locks`` in
    HANDOFF.md renders from."""
    lp = paths(project_root)
    if not lp.dir.exists():
        return []
    out = []
    for lock_path in sorted(lp.dir.glob("*.lock")):
        held_by = _read_record(lock_path)
        if held_by is not None:
            age_seconds = _age_seconds(held_by)
            out.append(
                {
                    "name": held_by.get("name", lock_path.stem),
                    "held_by": held_by,
                    "age_seconds": age_seconds,
                    "possibly_stale": age_seconds is not None and age_seconds > 3600,
                }
            )
    return out


def render_active_locks(project_root: str | os.PathLike[str]) -> str:
    """Markdown block for HANDOFF.md's ``## Active Locks`` section — the "clear
    whether occupied or not" view, visible through the same file agents already
    read before continuing work."""
    locks = list_locks(project_root)
    if not locks:
        return "- None held."
    lines = []
    for lock in locks:
        held_by = lock["held_by"]
        age = lock["age_seconds"]
        age_text = f"{int(age // 60)}m ago" if age is not None else "unknown"
        stale = " (possibly stale)" if lock["possibly_stale"] else ""
        task_text = f" — {held_by['task']}" if held_by.get("task") else ""
        lines.append(
            f"- **{lock['name']}**: held by `{held_by.get('agent', 'unknown')}` "
            f"since {age_text}{stale}{task_text}"
        )
    return "\n".join(lines)


def _record(*, name: str, agent: str, task: str) -> dict[str, Any]:
    now = datetime.now().astimezone()
    return {
        "name": name,
        "agent": agent,
        "task": task,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "acquired_at": now.isoformat(timespec="seconds"),
        "acquired_at_epoch": time.time(),
    }


def _read_record(lock_path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        # A lock file that exists but can't be parsed is still a lock (the OS-level
        # O_EXCL claim already happened) -- report it as held-by-unknown rather
        # than silently treating a corrupt file as "free" and double-claiming.
        return {"agent": "unknown (corrupt lock file)", "task": "", "acquired_at_epoch": None}


def _age_seconds(held_by: dict[str, Any]) -> float | None:
    epoch = held_by.get("acquired_at_epoch")
    return (time.time() - epoch) if isinstance(epoch, (int, float)) else None


def _archive(
    project_root: str | os.PathLike[str],
    name: str,
    held_by: dict[str, Any] | None,
    *,
    event: str,
    by: str,
    reason: str,
) -> None:
    lp = paths(project_root)
    lp.history.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    entry = {"event": event, "name": name, "by": by, "reason": reason, "held_by": held_by}
    (lp.history / f"{stamp}-{_slug(name)}-{event}.json").write_text(
        json.dumps(entry, indent=2), encoding="utf-8"
    )


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip().lower()).strip("-._")
    return slug[:64] or "lock"


__all__ = [
    "acquire",
    "release",
    "force_break",
    "status",
    "list_locks",
    "render_active_locks",
    "paths",
    "LockPaths",
]
