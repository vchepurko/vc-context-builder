"""Reader-side for the rotating JSONL audit log emitted by
``services/notify/log.py`` in the parent project.

This module never writes — it only walks the log files for the
query engine. Path is configurable via
``.vc-context/conventions.json``::

    {
        "notify_log": { "path": "logs/notify.jsonl" }
    }

If unset, defaults to ``logs/notify.jsonl`` at the project root.
Projects without an audit log degrade gracefully — search returns
``[]``, stats returns zeros.

Stdlib only, mirrors the rest of vc-context-builder.
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os
from typing import Any, Dict, Iterable, Iterator, List, Optional


DEFAULT_PATH = "logs/notify.jsonl"


def _resolve_paths(project_root: str, log_rel_path: str) -> List[str]:
    """``[base, base.YYYY-MM-DD, ...]`` — current file plus rotated
    companions. Empty when neither exists."""
    base = os.path.join(project_root, log_rel_path)
    out = [base] if os.path.isfile(base) else []
    out.extend(sorted(p for p in glob.glob(f"{base}.*") if os.path.isfile(p)))
    return out


def _load_log_path(project_root: str) -> str:
    """Resolve the JSONL path: conventions.json override → default."""
    conv_path = os.path.join(project_root, ".vc-context", "conventions.json")
    if os.path.isfile(conv_path):
        try:
            with open(conv_path, "r", encoding="utf-8") as fh:
                conv = json.load(fh)
            override = (
                conv.get("notify_log", {}).get("path")
                if isinstance(conv, dict) else None
            )
            if isinstance(override, str) and override:
                return override
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_PATH


def _parse_since(value: Optional[str]) -> Optional[float]:
    """``"7d" / "24h" / "2026-05-01"`` → epoch float (UTC). ``None``
    means no cutoff."""
    if not value:
        return None
    if value.endswith("d") and value[:-1].isdigit():
        return (
            dt.datetime.now(dt.UTC) - dt.timedelta(days=int(value[:-1]))
        ).timestamp()
    if value.endswith("h") and value[:-1].isdigit():
        return (
            dt.datetime.now(dt.UTC) - dt.timedelta(hours=int(value[:-1]))
        ).timestamp()
    try:
        if "T" in value:
            return dt.datetime.fromisoformat(value).timestamp()
        return dt.datetime.fromisoformat(value).replace(
            tzinfo=dt.UTC
        ).timestamp()
    except ValueError:
        return None


def _iter_records(paths: Iterable[str]) -> Iterator[dict]:
    """Yield decoded objects, skipping blanks and undecodable lines.

    A half-written record from a crash mid-write shouldn't break
    the search — same forgiving parser as the CLI.
    """
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue


def _matches(
    rec: dict,
    *,
    kind: Optional[str],
    recipient: Optional[int],
    channel: Optional[str],
    outcome: Optional[str],
    since_ts: Optional[float],
) -> bool:
    if kind and rec.get("kind") != kind:
        return False
    if recipient is not None and int(rec.get("recipient_uid", 0)) != recipient:
        return False
    if channel and rec.get("channel") != channel:
        return False
    if outcome and rec.get("outcome") != outcome:
        return False
    if since_ts is not None and float(rec.get("ts", 0)) < since_ts:
        return False
    return True


def search(
    project_root: str,
    *,
    kind: Optional[str] = None,
    recipient: Optional[int] = None,
    channel: Optional[str] = None,
    outcome: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Filtered records, newest-last (file order). ``limit`` caps the
    response so an MCP client doesn't accidentally pull megabytes of
    JSONL into the model context."""
    paths = _resolve_paths(project_root, _load_log_path(project_root))
    if not paths:
        return []
    since_ts = _parse_since(since)
    out: List[Dict[str, Any]] = []
    for rec in _iter_records(paths):
        if _matches(
            rec, kind=kind, recipient=recipient,
            channel=channel, outcome=outcome, since_ts=since_ts,
        ):
            out.append(rec)
            if limit and len(out) >= limit:
                break
    return out


def stats(
    project_root: str,
    *,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    """``{total, by_kind: {kind: {sent, failed, skipped}}, by_channel:
    {channel: {sent, failed, skipped}}}``."""
    paths = _resolve_paths(project_root, _load_log_path(project_root))
    if not paths:
        return {"total": 0, "by_kind": {}, "by_channel": {}}
    since_ts = _parse_since(since)
    total = 0
    by_kind: Dict[str, Dict[str, int]] = {}
    by_channel: Dict[str, Dict[str, int]] = {}
    for rec in _iter_records(paths):
        if since_ts is not None and float(rec.get("ts", 0)) < since_ts:
            continue
        outcome = rec.get("outcome", "?")
        kind_bucket = by_kind.setdefault(
            rec.get("kind", "?"),
            {"sent": 0, "failed": 0, "skipped": 0},
        )
        kind_bucket[outcome] = kind_bucket.get(outcome, 0) + 1
        ch_bucket = by_channel.setdefault(
            rec.get("channel", "?"),
            {"sent": 0, "failed": 0, "skipped": 0},
        )
        ch_bucket[outcome] = ch_bucket.get(outcome, 0) + 1
        total += 1
    return {"total": total, "by_kind": by_kind, "by_channel": by_channel}
