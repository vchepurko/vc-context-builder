"""Per-call telemetry sidecar for the MCP dispatcher.

Goal: give the user a way to *see* how the agent is using the MCP
surface — call counts, payload sizes, latency, "wasted" empty
results — without paying any meaningful runtime cost when nothing
is consuming the data.

Design — three pieces:

* :class:`MetricsWriter` — appends one JSON line per dispatcher
  call to ``~/.vc-context/metrics/<repo-hash>-<YYYY-MM-DD>.jsonl``.
  Failure to write is silently swallowed (a broken metrics path
  must never break the call site).
* :func:`read_metrics` — iterates the JSONL files belonging to one
  project and returns the parsed entries newer than ``since``.
* :func:`aggregate` — turns a list of entries into the summary
  shape consumed by ``QueryEngine.get_session_metrics`` and the
  CLI ``stats`` subcommand.

Token estimate: ``len(json.dumps(result_bytes)) // 4``.  Rough but
stable enough for trends and cheap to compute (no tiktoken
dependency — keeps vc-context stdlib-only).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from collections.abc import Iterable
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path / filename helpers
# ---------------------------------------------------------------------------


def _default_base_dir() -> str:
    """``~/.vc-context/metrics`` — overridable via ``VC_CONTEXT_METRICS_DIR``
    env var so power users / CI can redirect.
    """
    override = os.environ.get("VC_CONTEXT_METRICS_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".vc-context", "metrics")


def _repo_hash(project_root: str) -> str:
    """Stable, short hash so different repos don't share files."""
    p = os.path.abspath(project_root).encode("utf-8")
    return hashlib.sha1(p).hexdigest()[:8]


def _today_filename(project_root: str, base_dir: str) -> str:
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(base_dir, f"{_repo_hash(project_root)}-{today}.jsonl")


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def _is_empty(result: Any) -> bool:
    """Is this dispatcher result "empty" / "no useful information"?

    Conservative — treats ``None`` and any empty container as empty,
    plus ``{"total": 0}``-style summaries (used by violations tools
    when nothing matched).
    """
    if result is None:
        return True
    if isinstance(result, (list, tuple, str, dict)) and len(result) == 0:
        return True
    if isinstance(result, dict) and result.get("total") == 0:
        return True
    return False


def _approx_tokens(payload_bytes: int) -> int:
    """4-bytes-per-token heuristic. Off by ~30% on Cyrillic but
    consistent enough across calls that comparisons hold.
    """
    return payload_bytes // 4


# Argument keys whose *values* we keep in the metrics line.  Picked so
# the quality detectors can match repeated/related calls (same symbol,
# same file) without bloating the log with arbitrary user input.
_ARG_VALUE_KEYS = (
    "name",
    "symbol",
    "file",
    "file_path",
    "path",
    "role",
    "pattern",
    "decorator",
    "selector",
    "match_path",
)
_ARG_VALUE_MAX_CHARS = 100


# ---------------------------------------------------------------------------
# Baseline estimates — what would ``grep -rn`` + ``Read`` have cost?
# ---------------------------------------------------------------------------
#
# Per-tool heuristic of the bytes a Bash-only fallback would have
# returned for a typical query. Calibrated from real session
# telemetry — these are upper-bound-ish numbers, not exact.
#
# Lookup rule: if the result is empty (``_is_empty``), the baseline is
# 0 (Bash would also return nothing, no savings). Otherwise baseline
# is taken from this table; tools absent from the table contribute 0
# (we don't claim savings we can't reason about).
#
# Adding a new tool: pick the dominant Bash workflow that answers the
# same question. Typical buckets:
#   * Symbol/role lookup     → 1× grep + 1× Read     ≈ 3,000 B
#   * Reverse / forward calls → grep -rn (lots of hits)    ≈ 4,000 B
#   * Module/repo overview   → ls + cat several maps  ≈ 6-8,000 B
#   * Locale audit           → grep through locales/  ≈ 2-3,000 B
#   * Slice                  → full-file Read equivalent ≈ 8,000 B
#   * Single-file grep       → grep -n equivalent ≈ 2,000 B
#   * Check/run/format       → run the same command, baseline 0
#
_BASELINE_BYTES_PER_TOOL: Dict[str, int] = {
    # Symbol lookup
    "find_symbol": 3000,
    "find_symbols": 5000,
    "verify": 2500,
    "get_symbol_card": 3000,
    # Reverse / forward
    "find_call_sites": 4000,
    "who_calls": 3500,
    "get_callees": 2500,
    "get_decorated_with": 3000,
    "get_raised_exceptions": 2500,
    "logline_to_symbol": 2000,
    "get_changed_symbols": 3000,
    "inspect_class": 4000,
    # Roles / structure
    "find_by_role": 5000,
    "list_roles": 3000,
    "list_modules": 1500,
    "summarise_module": 8000,
    "repo_map": 6000,
    "impact": 6000,
    "get_file_card": 4000,
    # Tests
    "find_test": 3000,
    "tests_by_category": 4000,
    "classify_tests": 4000,
    "coverage_for_role": 3500,
    # Locales
    "list_locale_keys": 4000,
    "find_locale_key": 2500,
    "get_locale_key": 1500,
    # Routes / aiogram
    "route_callers": 3500,
    "route_for_js_call": 3000,
    "find_callback": 2500,
    "trace_fsm_flow": 5000,
    # Templates / Angular
    "find_in_templates": 3000,
    "ng_ajs_find": 3000,
    "ng_audit_component": 4000,
    "ng_inject_graph": 5000,
    "ng_list_routes": 3000,
    "ng_module_members": 4000,
    "ng_overview": 6000,
    "ng_route_for_path": 2000,
    "ng_routes_for_component": 2500,
    "ng_ts_class_shape": 3000,
    "ng_uses_selector": 2500,
    # Notify audit
    "notify_log_search": 3000,
    "notify_log_stats": 2500,
    # New gap-closers
    "find_pattern_in_configs": 3000,
    "list_config_kinds": 100,
    "devops_card": 8000,
    "find_orm_field_usage": 5000,
    # Single-file surgical reads — Bash equivalent is reading the whole file
    "read_slice": 8000,
    "find_in_file": 2000,
    # Angular lint — Bash equivalent is `npx eslint src/`
    "ng_eslint_violations": 5000,
    "ng_find_module": 2500,
    # No Bash-equivalent — baseline is 0
    "run_check": 0,
    "list_checks": 200,
    "lint_violations": 2000,
    "mypy_violations": 2000,
    "ruff_violations": 2000,
    "ruff_format": 0,
    "rebuild_index": 0,
    "get_session_metrics": 0,
}


def _baseline_bytes(tool: str, empty: bool) -> int:
    """Heuristic byte count for the Bash equivalent of ``tool``.
    Empty results don't earn savings — Bash would also have returned
    nothing for an unknown symbol / pattern."""
    if empty:
        return 0
    return _BASELINE_BYTES_PER_TOOL.get(tool, 0)


def _args_summary(args: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Pick the value-bearing arg keys so quality detectors can match
    repeated calls.  Strings are clamped to ``_ARG_VALUE_MAX_CHARS``;
    everything else is ignored.  Returns ``{}`` for non-dict input.
    """
    if not isinstance(args, dict):
        return {}
    out: Dict[str, str] = {}
    for k in _ARG_VALUE_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:_ARG_VALUE_MAX_CHARS]
    return out


class MetricsWriter:
    """Append-only JSONL writer scoped to one ``project_root``.

    Construction is cheap (just stores paths). The directory is
    created lazily on the first ``record()`` call so importing this
    module never has filesystem side-effects.

    ``agent_id`` is resolved from the ``MCP_AGENT_ID`` environment variable
    at construction time. If that variable is absent the writer accepts a
    richer name via :meth:`set_client_info` (called from the ``initialize``
    handler in ``rpc.py`` using the JSON-RPC ``clientInfo`` field).
    """

    def __init__(
        self,
        project_root: str,
        base_dir: Optional[str] = None,
    ) -> None:
        self.project_root = project_root
        self.base_dir = base_dir or _default_base_dir()
        self._ensured = False
        env_agent = os.environ.get("MCP_AGENT_ID", "").strip()
        self.agent_id: str = env_agent if env_agent else "unknown"

    def set_client_info(self, name: Optional[str], version: Optional[str] = None) -> None:
        """Update ``agent_id`` from the JSON-RPC ``initialize`` clientInfo.

        Only updates when the id is still "unknown" so an explicit
        ``MCP_AGENT_ID`` env var always wins.
        """
        if self.agent_id != "unknown":
            return
        if not name:
            return
        label = str(name).strip()
        if version:
            label = f"{label}/{str(version).strip()}"
        self.agent_id = label

    def _ensure_dir(self) -> bool:
        if self._ensured:
            return True
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            self._ensured = True
            return True
        except OSError:
            return False

    def record(
        self,
        tool: str,
        args: Optional[Dict[str, Any]],
        result: Any,
        t_ms: int,
        ok: bool,
    ) -> None:
        """Emit one telemetry line. Never raises — a busted metrics
        directory should never break the call site.
        """
        if not self._ensure_dir():
            return
        try:
            payload_str = json.dumps(result, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            payload_str = ""
        result_bytes = len(payload_str.encode("utf-8"))
        empty = _is_empty(result)
        baseline = _baseline_bytes(tool, empty)
        entry = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "tool": tool,
            "agent_id": self.agent_id,
            "args_keys": sorted(args.keys()) if isinstance(args, dict) else [],
            "args_summary": _args_summary(args),
            "result_bytes": result_bytes,
            "approx_tokens": _approx_tokens(result_bytes),
            # Heuristic: how many bytes a ``grep -rn`` + ``Read`` would
            # have returned for the same question. Empty results pin
            # baseline to 0 (Bash wouldn't have helped either).
            "baseline_bytes": baseline,
            "baseline_tokens": _approx_tokens(baseline),
            "t_ms": int(t_ms),
            "ok": bool(ok),
            "empty": empty,
        }
        path = _today_filename(self.project_root, self.base_dir)
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            return


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

_SINCE_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _parse_since(since: Optional[str]) -> Optional[_dt.datetime]:
    """Convert ``"24h"`` / ``"7d"`` / ``"today"`` / ``"all"`` into a
    UTC threshold (inclusive).  ``None`` and ``"all"`` mean no filter.
    """
    if since is None or not str(since).strip():
        return None
    s = str(since).strip().lower()
    if s in ("all", "any"):
        return None
    now = _dt.datetime.now(_dt.timezone.utc)
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    m = _SINCE_RE.match(s)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return now - _dt.timedelta(seconds=n * _UNIT_SECONDS[unit])


def _iter_files(project_root: str, base_dir: str) -> Iterable[str]:
    if not os.path.isdir(base_dir):
        return ()
    prefix = _repo_hash(project_root) + "-"
    out: List[Tuple[str, str]] = []
    for fname in os.listdir(base_dir):
        if not fname.startswith(prefix) or not fname.endswith(".jsonl"):
            continue
        out.append((fname, os.path.join(base_dir, fname)))
    # Sort by filename (date) ascending so older entries come first.
    out.sort()
    return [path for _, path in out]


def read_metrics(
    project_root: str,
    *,
    since: Optional[str] = None,
    base_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return every metrics line for ``project_root`` newer than
    ``since``. Malformed lines are skipped silently.
    """
    base = base_dir or _default_base_dir()
    threshold = _parse_since(since)
    out: List[Dict[str, Any]] = []
    for path in _iter_files(project_root, base):
        try:
            with open(path, encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if threshold is not None:
                        ts = entry.get("ts")
                        if not isinstance(ts, str):
                            continue
                        try:
                            ts_dt = _dt.datetime.fromisoformat(ts)
                        except ValueError:
                            continue
                        if ts_dt.tzinfo is None:
                            ts_dt = ts_dt.replace(tzinfo=_dt.timezone.utc)
                        if ts_dt < threshold:
                            continue
                    out.append(entry)
        except OSError:
            continue
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate(
    entries: List[Dict[str, Any]],
    *,
    group_by: str = "tool",
    baseline: bool = False,
) -> Dict[str, Any]:
    """Roll a list of entries into a summary dict.

    Keys:
        calls: total entries.
        total_tokens: sum of ``approx_tokens``.
        total_bytes: sum of ``result_bytes`` (more precise than tokens).
        avg_t_ms: mean latency.
        empty_ratio: 0..1, fraction of calls returning empty data.
        ok_ratio: 0..1, fraction that didn't raise.
        by_<group>: {key → {calls, tokens, avg_t_ms, empty_ratio}}.
        baseline (only when ``baseline=True``): heuristic savings vs
            a Bash-only fallback. See :data:`_BASELINE_BYTES_PER_TOOL`
            for per-tool estimates.
    """
    if not entries:
        out: Dict[str, Any] = {
            "calls": 0,
            "total_tokens": 0,
            "total_bytes": 0,
            "avg_t_ms": 0.0,
            "empty_ratio": 0.0,
            "ok_ratio": 1.0,
            f"by_{group_by}": {},
        }
        if baseline:
            out["baseline"] = {
                "total_baseline_tokens": 0,
                "total_baseline_bytes": 0,
                "saved_tokens": 0,
                "saved_bytes": 0,
                "savings_ratio": 0.0,
                "by_tool": {},
                "note": "Heuristic: per-tool estimate of grep+Read equivalent.",
            }
        return out

    total_calls = len(entries)
    total_tokens = sum(int(e.get("approx_tokens", 0)) for e in entries)
    total_bytes = sum(int(e.get("result_bytes", 0)) for e in entries)
    total_ms = sum(int(e.get("t_ms", 0)) for e in entries)
    empty_calls = sum(1 for e in entries if e.get("empty"))
    ok_calls = sum(1 for e in entries if e.get("ok"))

    def _key(entry: Dict[str, Any]) -> str:
        if group_by == "tool":
            return str(entry.get("tool") or "?")
        if group_by == "hour":
            ts = str(entry.get("ts") or "")
            return ts[:13] if len(ts) >= 13 else "?"  # "YYYY-MM-DDTHH"
        if group_by == "empty":
            return "empty" if entry.get("empty") else "non-empty"
        return str(entry.get(group_by) or "?")

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for e in entries:
        buckets.setdefault(_key(e), []).append(e)

    by: Dict[str, Dict[str, Any]] = {}
    for key, group in buckets.items():
        n = len(group)
        toks = sum(int(e.get("approx_tokens", 0)) for e in group)
        ms = sum(int(e.get("t_ms", 0)) for e in group)
        empties = sum(1 for e in group if e.get("empty"))
        by[key] = {
            "calls": n,
            "tokens": toks,
            "avg_t_ms": round(ms / n, 1) if n else 0.0,
            "empty_ratio": round(empties / n, 3) if n else 0.0,
        }

    result: Dict[str, Any] = {
        "calls": total_calls,
        "total_tokens": total_tokens,
        "total_bytes": total_bytes,
        "avg_t_ms": round(total_ms / total_calls, 1),
        "empty_ratio": round(empty_calls / total_calls, 3),
        "ok_ratio": round(ok_calls / total_calls, 3),
        f"by_{group_by}": by,
    }

    if baseline:
        # Older log entries don't carry ``baseline_bytes`` — fall back
        # to the per-tool estimate so the same aggregator works on
        # historical telemetry without a re-record.
        def _entry_baseline(entry: Dict[str, Any]) -> int:
            if "baseline_bytes" in entry:
                return int(entry.get("baseline_bytes") or 0)
            return _baseline_bytes(str(entry.get("tool") or ""), bool(entry.get("empty")))

        total_baseline_bytes = sum(_entry_baseline(e) for e in entries)
        total_baseline_tokens = _approx_tokens(total_baseline_bytes)
        saved_bytes = max(0, total_baseline_bytes - total_bytes)
        saved_tokens = max(0, total_baseline_tokens - total_tokens)
        savings_ratio = (
            round(saved_bytes / total_baseline_bytes, 3) if total_baseline_bytes else 0.0
        )

        # Per-tool breakdown: which tools save the most.
        by_tool_savings: Dict[str, Dict[str, int]] = {}
        for tool_name, group in buckets.items() if group_by == "tool" else []:
            if group_by != "tool":
                break
            bbytes = sum(_entry_baseline(e) for e in group)
            actual_bytes = sum(int(e.get("result_bytes", 0)) for e in group)
            by_tool_savings[tool_name] = {
                "baseline_bytes": bbytes,
                "actual_bytes": actual_bytes,
                "saved_bytes": max(0, bbytes - actual_bytes),
                "saved_tokens": _approx_tokens(max(0, bbytes - actual_bytes)),
            }

        result["baseline"] = {
            "total_baseline_tokens": total_baseline_tokens,
            "total_baseline_bytes": total_baseline_bytes,
            "saved_tokens": saved_tokens,
            "saved_bytes": saved_bytes,
            "savings_ratio": savings_ratio,
            "by_tool": by_tool_savings,
            "note": (
                "Heuristic: per-tool estimate of grep+Read equivalent. "
                "Tools without a Bash-fallback (read_slice, run_check, "
                "rebuild_index, get_session_metrics) contribute 0. "
                "Empty results also contribute 0."
            ),
        }
    return result
