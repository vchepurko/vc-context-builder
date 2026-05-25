"""Real benchmark: MCP tools vs Bash equivalents.

Measures wall-clock time and output bytes for identical questions,
answered two ways. Run from the .ai-context/ directory:

    python3 benchmark.py [--root /path/to/project]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

ROOT = os.path.abspath(os.path.join(_HERE, ".."))


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------


@dataclass
class Result:
    method: str  # "mcp" | "bash"
    tool_or_cmd: str
    ms: float
    bytes_out: int
    tokens: int  # bytes // 4
    found: bool  # non-empty result


def _run_bash(cmd: str, cwd: str = ROOT) -> Tuple[float, int, bool]:
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, timeout=30)
        out = (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        out = "TIMEOUT"
    ms = (time.monotonic() - t0) * 1000
    b = len(out.encode("utf-8"))
    return ms, b, bool(out and out != "TIMEOUT")


def _run_mcp(fn: Callable[[], Any]) -> Tuple[float, int, bool]:
    t0 = time.monotonic()
    try:
        result = fn()
    except Exception as e:
        result = {"error": str(e)}
    ms = (time.monotonic() - t0) * 1000
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    b = len(serialized.encode("utf-8"))
    return ms, b, bool(result)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@dataclass
class Task:
    name: str
    difficulty: str  # easy | medium | hard
    mcp_label: str
    bash_label: str
    mcp_fn: Callable[[], Any]
    bash_cmd: str


def build_tasks(engine: Any) -> List[Task]:
    return [
        # ── EASY ──────────────────────────────────────────────────────────
        # Tasks 1-2 use include_body=False — the question is "where is it
        # defined?", not "show me the code". Agents that need the source
        # can pass include_body=True (or rely on conventions.json default).
        Task(
            name="Where is a service defined? (location only)",
            difficulty="easy",
            mcp_label="find_symbol('CollectionPlayerStateService', include_body=False)",
            bash_label="grep -rn 'class CollectionPlayerStateService' src/",
            mcp_fn=lambda: engine.find_symbol("CollectionPlayerStateService", include_body=False),
            bash_cmd="grep -rn 'class CollectionPlayerStateService' src/",
        ),
        Task(
            name="Find interface by I-prefix (location only)",
            difficulty="easy",
            mcp_label="find_symbol('ILearningObjectRegistration', include_body=False)",
            bash_label="grep -rn 'interface ILearningObjectRegistration' src/",
            mcp_fn=lambda: engine.find_symbol("ILearningObjectRegistration", include_body=False),
            bash_cmd="grep -rn 'interface ILearningObjectRegistration' src/",
        ),
        Task(
            name="Find symbol by camelCase name (location only)",
            difficulty="easy",
            mcp_label="find_symbol('collectionNavigationService', include_body=False)",
            bash_label="grep -rn -i 'class collectionnavigationservice' src/",
            mcp_fn=lambda: engine.find_symbol("collectionNavigationService", include_body=False),
            bash_cmd="grep -rn -i 'class collectionnavigationservice' src/",
        ),
        # ── MEDIUM ────────────────────────────────────────────────────────
        Task(
            name="Where is a service injected? (DI lookup)",
            difficulty="medium",
            mcp_label=(
                "find_call_sites('CollectionPlayerStateService', "
                "match_path='src/app/modules/collection-player-v2/**')"
            ),
            bash_label=(
                "grep -rn 'CollectionPlayerStateService' "
                "src/app/modules/collection-player-v2 | grep -v spec | grep 'private\\|inject'"
            ),
            mcp_fn=lambda: engine.find_call_sites(
                "CollectionPlayerStateService",
                match_path="src/app/modules/collection-player-v2/**",
            ),
            bash_cmd=(
                "grep -rn 'CollectionPlayerStateService' "
                "src/app/modules/collection-player-v2 "
                "--include='*.ts' | grep -v spec | grep 'private\\|inject'"
            ),
        ),
        Task(
            name="Angular component audit",
            difficulty="medium",
            mcp_label="ng_audit_component('CollectionCoursePlayerComponent')",
            bash_label="cat src/.../collection-course-player.component.ts",
            mcp_fn=lambda: engine.ng_audit_component("CollectionCoursePlayerComponent"),
            bash_cmd=(
                "cat src/app/modules/collection-player-v2/components/"
                "collection-course-player/collection-course-player.component.ts"
            ),
        ),
        Task(
            name="Find AJS registration",
            difficulty="medium",
            mcp_label="ng_ajs_find('coursePlayer')",
            bash_label="grep -rn \".component('coursePlayer'\" app/ src/",
            mcp_fn=lambda: engine.ng_ajs_find("coursePlayer"),
            bash_cmd="grep -rn \".component('coursePlayer'\" app/ src/ 2>/dev/null",
        ),
        Task(
            name="NgModule members",
            difficulty="medium",
            mcp_label="ng_module_members('CollectionPlayerV2Module')",
            bash_label="cat src/.../collection-player-v2.module.ts",
            mcp_fn=lambda: engine.ng_module_members("CollectionPlayerV2Module"),
            bash_cmd=("cat src/app/modules/collection-player-v2/collection-player-v2.module.ts"),
        ),
        # ── HARD ──────────────────────────────────────────────────────────
        Task(
            name="Code health check (lint + type + format)",
            difficulty="hard",
            mcp_label="check_health()",
            bash_label="npm run lint 2>&1 | tail -5  (lint only, no type/format)",
            mcp_fn=lambda: engine.check_health(summary=True),
            bash_cmd="npm run lint 2>&1 | tail -5",
        ),
        Task(
            name="Who uses this service across whole project?",
            difficulty="hard",
            mcp_label="who_calls('CollectionNavigationService')",
            bash_label=(
                "grep -rn 'CollectionNavigationService' src/ --include='*.ts' | grep -v spec"
            ),
            mcp_fn=lambda: engine.who_calls("CollectionNavigationService"),
            bash_cmd=(
                "grep -rn 'CollectionNavigationService' src/ --include='*.ts' | grep -v spec"
            ),
        ),
        Task(
            name="Full DI inject graph for a module",
            difficulty="hard",
            mcp_label="ng_inject_graph('CollectionPlayerV2Module')",
            bash_label=(
                "grep -rn 'constructor\\|inject(' src/app/modules/collection-player-v2 "
                "--include='*.ts' | grep -v spec"
            ),
            mcp_fn=lambda: engine.ng_inject_graph("CollectionPlayerV2Module"),
            bash_cmd=(
                "grep -rn 'constructor\\|inject(' "
                "src/app/modules/collection-player-v2 --include='*.ts' | grep -v spec"
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_benchmark(tasks: List[Task], root: str) -> List[Dict[str, Any]]:
    results = []
    for task in tasks:
        print(f"  [{task.difficulty.upper()}] {task.name}...", end=" ", flush=True)

        mcp_ms, mcp_b, mcp_found = _run_mcp(task.mcp_fn)
        bash_ms, bash_b, bash_found = _run_bash(task.bash_cmd, cwd=root)

        mcp_tok = mcp_b // 4
        bash_tok = bash_b // 4
        saved = bash_tok - mcp_tok
        speedup = bash_ms / mcp_ms if mcp_ms > 0 else 0

        print(
            f"MCP {mcp_tok}T/{mcp_ms:.0f}ms  Bash {bash_tok}T/{bash_ms:.0f}ms  "
            f"saved={saved:+d}T  speed={speedup:.1f}x"
        )

        results.append(
            {
                "task": task.name,
                "difficulty": task.difficulty,
                "mcp": {
                    "label": task.mcp_label,
                    "ms": round(mcp_ms, 1),
                    "bytes": mcp_b,
                    "tokens": mcp_tok,
                    "found": mcp_found,
                },
                "bash": {
                    "label": task.bash_label,
                    "ms": round(bash_ms, 1),
                    "bytes": bash_b,
                    "tokens": bash_tok,
                    "found": bash_found,
                },
                "saved_tokens": saved,
                "speedup_x": round(speedup, 1),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _md_table(results: List[Dict[str, Any]]) -> str:
    lines = [
        "| # | Task | Difficulty | MCP tokens | MCP ms | Bash tokens | Bash ms | Saved | Speed |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        saved = r["saved_tokens"]
        saved_str = f"+{saved}" if saved > 0 else str(saved)
        mcp_found = "✓" if r["mcp"]["found"] else "✗"
        bash_found = "✓" if r["bash"]["found"] else "✗"
        lines.append(
            f"| {i} | {r['task']} | {r['difficulty']} "
            f"| {r['mcp']['tokens']} {mcp_found} | {r['mcp']['ms']:.0f} "
            f"| {r['bash']['tokens']} {bash_found} | {r['bash']['ms']:.0f} "
            f"| {saved_str} | {r['speedup_x']}× |"
        )
    return "\n".join(lines)


def _summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_mcp = sum(r["mcp"]["tokens"] for r in results)
    total_bash = sum(r["bash"]["tokens"] for r in results)
    total_saved = sum(r["saved_tokens"] for r in results)
    avg_speed = sum(r["speedup_x"] for r in results) / len(results)
    savings_ratio = total_saved / total_bash if total_bash else 0
    return {
        "tasks": len(results),
        "total_mcp_tokens": total_mcp,
        "total_bash_tokens": total_bash,
        "total_saved_tokens": total_saved,
        "savings_ratio": round(savings_ratio, 2),
        "avg_speedup_x": round(avg_speed, 1),
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--root", default=ROOT)
    p.add_argument("--json-out", default=None)
    args = p.parse_args()

    from query_engine import QueryEngine

    engine = QueryEngine(args.root)

    print(f"\nBenchmark — project: {args.root}\n")
    tasks = build_tasks(engine)
    results = run_benchmark(tasks, root=args.root)

    s = _summary(results)
    print("\n=== Summary ===")
    print(f"Tasks: {s['tasks']}")
    print(f"MCP total tokens : {s['total_mcp_tokens']}")
    print(f"Bash total tokens: {s['total_bash_tokens']}")
    print(f"Saved            : {s['total_saved_tokens']} ({s['savings_ratio'] * 100:.0f}%)")
    print(f"Avg speedup      : {s['avg_speedup_x']}×")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"summary": s, "results": results}, fh, indent=2)
        print(f"\nJSON saved to {args.json_out}")
    else:
        print("\n" + _md_table(results))
