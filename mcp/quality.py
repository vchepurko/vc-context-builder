"""Phase-2 quality signals on top of the Phase-1 telemetry sidecar.

Three detectors run over the same JSONL stream that
:class:`mcp.metrics.MetricsWriter` produces — no new writer schema,
no new artefacts.  Each detector returns a list of *findings*, where
every finding carries enough evidence (timestamps + tool calls) to
audit the claim later.

Findings shape::

    {
        "kind": "<wasteful_pair|hot_reread|empty_streak>",
        "severity": "info" | "warn",
        "message": "<one-line human summary>",
        "evidence": [<entry-or-pointer>, ...],
        ...kind-specific fields...
    }

Detectors are conservative — false positives waste user attention more
than false negatives.  Tunables (window seconds, repeat thresholds)
live as module constants so the test suite can exercise edge cases
without poking private state.
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter
from typing import Any, Dict, List, Optional

# --- Tunables ---------------------------------------------------------------

# Window in which a `find_symbol` followed by `read_slice` of the same
# file counts as wasteful — past this, the agent likely had a fresh
# reason to read.
WASTEFUL_PAIR_WINDOW_SEC = 60

# How many times the same arg-summary value can recur for one tool
# before we flag a hot reread.
HOT_REREAD_THRESHOLD = 3

# Empty-streak threshold: this many consecutive empty calls of the
# *same* tool, with the same kind of question, looks like the agent
# is barking up the wrong tree.
EMPTY_STREAK_THRESHOLD = 3


# --- Helpers ---------------------------------------------------------------

def _ts(entry: Dict[str, Any]) -> Optional[_dt.datetime]:
    raw = entry.get("ts")
    if not isinstance(raw, str):
        return None
    try:
        out = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=_dt.timezone.utc)
    return out


def _entry_summary(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Project an entry to the minimal fields a finding needs as
    evidence — keeps the response payload tight.
    """
    return {
        "ts": entry.get("ts"),
        "tool": entry.get("tool"),
        "args_summary": entry.get("args_summary") or {},
        "empty": bool(entry.get("empty")),
        "result_bytes": entry.get("result_bytes", 0),
    }


# --- Detectors --------------------------------------------------------------

def detect_wasteful_pairs(
    entries: List[Dict[str, Any]],
    window_sec: int = WASTEFUL_PAIR_WINDOW_SEC,
) -> List[Dict[str, Any]]:
    """Flag a `find_symbol(name=X)` immediately followed by a
    `read_slice(file=...)` against the SAME ``name``-scoped file when
    the find_symbol call did NOT pass ``include_body=true``.

    Why it's wasteful: ``find_symbol(..., include_body=true)`` would
    have returned the body in the same round-trip.  We don't know the
    file from `find_symbol` because we don't capture its result; we
    look for proximity (same agent step) and the same ``name`` /
    ``symbol`` carrier — ``read_slice`` calls usually follow up the
    last find with `file=` set to the result.

    Conservative match — pair only when:
      - both entries succeeded (``ok=true``)
      - the find_symbol args did NOT carry ``include_body``
      - the read_slice happened within ``window_sec`` of find_symbol
      - the find_symbol's name is non-empty.
    """
    findings: List[Dict[str, Any]] = []
    pending: Optional[Dict[str, Any]] = None

    for e in entries:
        if not e.get("ok"):
            pending = None
            continue
        tool = e.get("tool")
        if tool == "find_symbol":
            args_keys = e.get("args_keys") or []
            args_summary = e.get("args_summary") or {}
            if "include_body" in args_keys:
                pending = None
                continue
            if not args_summary.get("name"):
                pending = None
                continue
            pending = e
            continue
        if tool == "read_slice" and pending is not None:
            t_pending = _ts(pending)
            t_now = _ts(e)
            if t_pending and t_now and (t_now - t_pending).total_seconds() <= window_sec:
                findings.append({
                    "kind": "wasteful_pair",
                    "severity": "info",
                    "message": (
                        f"find_symbol({pending['args_summary'].get('name')!r}) "
                        f"→ read_slice within {window_sec}s; could have used "
                        f"include_body=true"
                    ),
                    "symbol": pending["args_summary"].get("name"),
                    "evidence": [_entry_summary(pending), _entry_summary(e)],
                })
            pending = None
            continue
        # Any other tool resets the buffer — we only catch immediate
        # follow-ups.
        pending = None

    return findings


def detect_hot_rereads(
    entries: List[Dict[str, Any]],
    threshold: int = HOT_REREAD_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Flag (tool, arg-value) pairs queried at least ``threshold``
    times in the window.

    Practical examples:
      - ``find_symbol(name="QueryEngine")`` × 5 — agent should cache
        the result instead of asking again.
      - ``get_callees(symbol="X")`` × 4 — same.

    The detector groups by ``(tool, args_summary)``.  Empty
    args_summary is skipped (``list_roles`` etc. legitimately recur).
    """
    counts: Counter = Counter()
    by_key: Dict[tuple, List[Dict[str, Any]]] = {}

    for e in entries:
        if not e.get("ok"):
            continue
        args_summary = e.get("args_summary") or {}
        if not args_summary:
            continue
        key = (e.get("tool"), tuple(sorted(args_summary.items())))
        counts[key] += 1
        by_key.setdefault(key, []).append(e)

    findings: List[Dict[str, Any]] = []
    for key, n in counts.most_common():
        if n < threshold:
            break  # Counter.most_common is sorted desc — once below, done.
        tool, args_tuple = key
        args = dict(args_tuple)
        findings.append({
            "kind": "hot_reread",
            "severity": "warn",
            "message": (
                f"{tool}({args}) called {n}× — consider caching"
            ),
            "tool": tool,
            "args_summary": args,
            "count": n,
            "evidence": [_entry_summary(e) for e in by_key[key][:3]],
        })
    return findings


def detect_empty_streaks(
    entries: List[Dict[str, Any]],
    threshold: int = EMPTY_STREAK_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Flag runs of ``threshold``+ consecutive empty results from the
    same tool — a smell that the agent is calling the wrong API or
    using a misspelled symbol.

    Streaks reset when a different tool is called or a non-empty
    result lands.  Each finding cites the head + tail of the streak.
    """
    findings: List[Dict[str, Any]] = []
    streak: List[Dict[str, Any]] = []
    streak_tool: Optional[str] = None

    def _flush() -> None:
        if len(streak) >= threshold:
            findings.append({
                "kind": "empty_streak",
                "severity": "warn",
                "message": (
                    f"{streak_tool} returned empty {len(streak)} times "
                    f"in a row — wrong query or misspelled symbol?"
                ),
                "tool": streak_tool,
                "count": len(streak),
                "evidence": [
                    _entry_summary(streak[0]),
                    _entry_summary(streak[-1]),
                ],
            })

    for e in entries:
        tool = e.get("tool")
        empty = bool(e.get("empty"))
        if empty and (streak_tool is None or streak_tool == tool):
            streak_tool = tool
            streak.append(e)
        else:
            _flush()
            streak = [e] if empty else []
            streak_tool = tool if empty else None
    _flush()
    return findings


# --- Aggregator -------------------------------------------------------------

def quality_report(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Bundle every detector's findings + a compact severity summary.

    Returns::

        {
            "total_findings": int,
            "by_severity": {"info": int, "warn": int},
            "wasteful_pairs": [...],
            "hot_rereads": [...],
            "empty_streaks": [...],
        }
    """
    pairs = detect_wasteful_pairs(entries)
    rereads = detect_hot_rereads(entries)
    streaks = detect_empty_streaks(entries)
    all_findings = pairs + rereads + streaks
    by_sev: Dict[str, int] = {"info": 0, "warn": 0}
    for f in all_findings:
        by_sev[f.get("severity", "info")] = by_sev.get(f.get("severity", "info"), 0) + 1
    return {
        "total_findings": len(all_findings),
        "by_severity": by_sev,
        "wasteful_pairs": pairs,
        "hot_rereads": rereads,
        "empty_streaks": streaks,
    }
