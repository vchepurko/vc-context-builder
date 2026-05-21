"""Real usage statistics from MCP telemetry.

Reads all ``~/.vc-context/metrics/<hash>-<date>.jsonl`` files and
emits a human-readable report. Run from anywhere:

    python3 .ai-context/usage_report.py
    python3 .ai-context/usage_report.py --since 7d
    python3 .ai-context/usage_report.py --md          # markdown output
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List


def _load_calls(metrics_dir: str, since_days: int | None = None) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    cutoff: datetime | None = None
    if since_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    for fname in sorted(os.listdir(metrics_dir)):
        if not fname.endswith(".jsonl"):
            continue
        with open(os.path.join(metrics_dir, fname), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    c = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cutoff and "ts" in c:
                    try:
                        ts = datetime.fromisoformat(c["ts"])
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if ts < cutoff:
                            continue
                    except ValueError:
                        pass
                calls.append(c)
    return calls


def _analyse(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_tool: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "tokens": 0, "baseline": 0, "empty": 0, "ms": 0.0}
    )
    for c in calls:
        tool = c.get("tool", "?")
        d = by_tool[tool]
        d["count"] += 1
        d["tokens"] += c.get("approx_tokens", c.get("result_bytes", 0) // 4)
        d["baseline"] += c.get("baseline_tokens", 0)
        if c.get("empty"):
            d["empty"] += 1
        d["ms"] += c.get("t_ms", 0.0)

    total = len(calls)
    total_tok = sum(d["tokens"] for d in by_tool.values())
    total_base = sum(d["baseline"] for d in by_tool.values())

    tools = []
    for tool, d in sorted(by_tool.items(), key=lambda x: -x[1]["count"]):
        n = d["count"]
        tools.append(
            {
                "tool": tool,
                "count": n,
                "share_pct": round(n / total * 100, 1) if total else 0,
                "avg_tokens": d["tokens"] // n if n else 0,
                "avg_baseline": d["baseline"] // n if n else 0,
                "saved_per_call": (d["baseline"] - d["tokens"]) // n if n else 0,
                "total_saved": d["baseline"] - d["tokens"],
                "empty_pct": round(d["empty"] / n * 100, 1) if n else 0,
                "avg_ms": round(d["ms"] / n, 1) if n else 0,
            }
        )

    dates = sorted(c["ts"][:10] for c in calls if "ts" in c)
    return {
        "total_calls": total,
        "date_from": dates[0] if dates else "—",
        "date_to": dates[-1] if dates else "—",
        "total_tokens": total_tok,
        "total_baseline": total_base,
        "total_saved": total_base - total_tok,
        "empty_calls": sum(1 for c in calls if c.get("empty")),
        "tools": tools,
    }


def _print_report(stats: Dict[str, Any], markdown: bool = False) -> None:
    total = stats["total_calls"]
    empty_pct = round(stats["empty_calls"] / total * 100) if total else 0

    if markdown:
        print(f"## MCP Usage Report — {stats['date_from']} → {stats['date_to']}\n")
        print(f"**Total calls:** {total:,}  |  "
              f"**Empty rate:** {empty_pct}%  |  "
              f"**Tokens used:** {stats['total_tokens']:,}  |  "
              f"**Tokens saved vs grep baseline:** "
              f"{stats['total_saved']:+,}\n")
        print("### Frequency")
        print(f"| Tool | Calls | Share | Avg T | Empty% | Avg ms | Saved/call |")
        print(f"|---|---|---|---|---|---|---|")
        for t in stats["tools"]:
            saved = f"{t['saved_per_call']:+}" if t["saved_per_call"] != 0 else "—"
            empty = f"**{t['empty_pct']}%**" if t["empty_pct"] >= 40 else f"{t['empty_pct']}%"
            print(f"| `{t['tool']}` | {t['count']} | {t['share_pct']}% "
                  f"| {t['avg_tokens']}T | {empty} | {t['avg_ms']}ms | {saved}T |")
    else:
        print(f"\nUsage report: {stats['date_from']} → {stats['date_to']}")
        print(f"Calls: {total:,}  |  Empty: {stats['empty_calls']} ({empty_pct}%)"
              f"  |  Tokens: {stats['total_tokens']:,}")
        print(f"Saved vs grep baseline: {stats['total_saved']:+,} T\n")
        print(f"{'Tool':<42} {'Calls':>5} {'Share':>6} {'AvgT':>6} "
              f"{'Empty':>6} {'AvgMs':>7} {'Saved/c':>9}")
        print("-" * 88)
        for t in stats["tools"]:
            flag = " !" if t["empty_pct"] >= 40 else "  "
            print(
                f"{t['tool']:<42}{flag}"
                f"{t['count']:>5} {t['share_pct']:>5.1f}% "
                f"{t['avg_tokens']:>6} {t['empty_pct']:>5.0f}% "
                f"{t['avg_ms']:>7.0f} {t['saved_per_call']:>+9}"
            )

    # Top savers
    savers = sorted(stats["tools"], key=lambda x: -x["total_saved"])
    if markdown:
        print("\n### Top token savers (total across all calls)")
        print("| Tool | Total saved | Calls | Saved/call |")
        print("|---|---|---|---|")
        for t in savers[:8]:
            if t["total_saved"] > 0:
                print(f"| `{t['tool']}` | +{t['total_saved']:,}T "
                      f"| {t['count']} | +{t['saved_per_call']}T |")
    else:
        print("\nTop token savers:")
        for t in savers[:8]:
            if t["total_saved"] > 0:
                print(f"  {t['tool']:<42} +{t['total_saved']:>8,} T  ({t['count']} calls)")

    # High empty rate tools
    high_empty = [t for t in stats["tools"] if t["empty_pct"] >= 40 and t["count"] >= 5]
    if high_empty:
        if markdown:
            print("\n### High empty rate (≥40%, ≥5 calls) — investigate or fix")
            print("| Tool | Calls | Empty% |")
            print("|---|---|---|")
            for t in sorted(high_empty, key=lambda x: -x["empty_pct"]):
                print(f"| `{t['tool']}` | {t['count']} | {t['empty_pct']}% |")
        else:
            print("\nHigh empty rate (≥40%, ≥5 calls) — may need fixing:")
            for t in sorted(high_empty, key=lambda x: -x["empty_pct"]):
                print(f"  {t['tool']:<42} {t['empty_pct']:.0f}% empty  ({t['count']} calls)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="MCP usage statistics")
    p.add_argument("--since", default=None, help="Number of days back (e.g. 7)")
    p.add_argument("--md", action="store_true", help="Markdown output")
    args = p.parse_args()

    metrics_dir = os.environ.get(
        "VC_CONTEXT_METRICS_DIR",
        os.path.expanduser("~/.vc-context/metrics/"),
    )
    if not os.path.isdir(metrics_dir):
        print(f"No metrics directory: {metrics_dir}")
        raise SystemExit(1)

    since_days = int(args.since) if args.since else None
    calls = _load_calls(metrics_dir, since_days=since_days)
    if not calls:
        print("No calls found.")
        raise SystemExit(0)

    stats = _analyse(calls)
    _print_report(stats, markdown=args.md)
