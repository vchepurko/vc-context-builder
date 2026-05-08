"""Command-line interface for the vc-context query engine.

Usage
-----
    vc-context find <symbol>     # symbol record
    vc-context calls <symbol>    # who calls
    vc-context role <role>       # all symbols with this role
    vc-context module <path>     # tight folder summary
    vc-context roles             # role -> count
    vc-context modules           # module folders
    vc-context build             # run the builder

``--json`` prints raw JSON, otherwise human-readable text. Exit code
``1`` when a lookup misses (symbol/role/module not found).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

# Make sibling imports work whether invoked as
# ``python3 .ai-context/cli.py ...`` or via the bin wrapper.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from query_engine import QueryEngine

# ----------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------


def _emit_json(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, sort_keys=False)
    sys.stdout.write("\n")


def _print_symbol(name: str, entry: Dict[str, Any]) -> None:
    print(f"{name}")
    # File + line range come first — most agents/devs need them next.
    file_v = entry.get("file")
    if file_v:
        line_v = entry.get("line")
        end_v = entry.get("end_line")
        suffix = ""
        if line_v:
            suffix = f":{line_v}"
            if end_v and end_v != line_v:
                suffix += f"-{end_v}"
        print(f"  file: {file_v}{suffix}")
    for key in ("kind", "params", "role"):
        if entry.get(key):
            print(f"  {key}: {entry[key]}")
    if entry.get("doc"):
        print("  doc:")
        for line in str(entry["doc"]).splitlines():
            print(f"    {line}")
    test = entry.get("test")
    if isinstance(test, dict) and test.get("test_file"):
        line = test.get("line")
        suffix = f":{line}" if line else ""
        print(f"  test: {test.get('test_file')}{suffix}  ({test.get('test_function')})")
    # Fact fields only show when explicitly requested via --fields
    # (HIDE_BY_DEFAULT keeps them off the lean response).
    callees = entry.get("callees")
    if callees:
        print(
            f"  callees ({len(callees)}): {', '.join(callees[:8])}"
            + (" ..." if len(callees) > 8 else "")
        )
    raises = entry.get("raises")
    if raises:
        print(f"  raises: {', '.join(raises)}")
    body = entry.get("body")
    if body:
        print("  body:")
        for line in str(body).splitlines():
            print(f"    {line}")


def _print_callers(symbol: str, callers: List[Dict[str, str]]) -> None:
    if not callers:
        print(f"No callers found for {symbol}.")
        return
    print(f"{len(callers)} possible caller(s) for {symbol}:")
    for entry in callers:
        kind = entry.get("kind") or "file"
        print(f"  {entry['file']}  [{kind}]")


def _print_role(role: str, names: List[str]) -> None:
    if not names:
        print(f"No symbols tagged with role '{role}'.")
        return
    print(f"{len(names)} symbol(s) with role '{role}':")
    for n in names:
        print(f"  {n}")


def _print_module(folder: str, summary: Dict[str, Any]) -> None:
    print(f"Module: {summary.get('directory') or folder}")
    files = summary.get("files") or {}
    if not files:
        print("  (no files indexed)")
        return
    for fname, fdata in files.items():
        print(f"  {fname}")
        deps = fdata.get("dependencies") or []
        if deps:
            print(f"    dependencies: {', '.join(deps)}")
        for exp in fdata.get("exports") or []:
            if not isinstance(exp, dict):
                print(f"    {exp}")
                continue
            tag = exp.get("kind") or ""
            role = f" ({exp['role']})" if exp.get("role") else ""
            doc = exp.get("doc")
            line = f"    - {exp.get('name')} [{tag}]{role}"
            if doc:
                line += f"  -- {doc}"
            print(line)


def _print_roles(counts: Dict[str, int]) -> None:
    if not counts:
        print("No roles indexed.")
        return
    width = max(len(r) for r in counts)
    for role in sorted(counts):
        print(f"  {role.ljust(width)}  {counts[role]}")


def _print_modules(modules: List[str]) -> None:
    if not modules:
        print("No modules indexed.")
        return
    for m in modules:
        print(f"  {m}")


# ----------------------------------------------------------------------
# Subcommand handlers
# ----------------------------------------------------------------------


def _engine(args: argparse.Namespace) -> QueryEngine:
    return QueryEngine(args.root)


def cmd_find(args: argparse.Namespace) -> int:
    engine = _engine(args)
    fields = (
        [f.strip() for f in args.fields.split(",") if f.strip()]
        if getattr(args, "fields", None)
        else None
    )
    entry = engine.find_symbol(
        args.symbol,
        fields=fields,
        include_body=bool(getattr(args, "body", False)),
    )
    if entry is None:
        if args.json:
            _emit_json(None)
        else:
            print(f"Symbol not found: {args.symbol}", file=sys.stderr)
        return 1
    if args.json:
        _emit_json({"name": args.symbol, **entry})
    else:
        _print_symbol(args.symbol, entry)
    return 0


def cmd_callees(args: argparse.Namespace) -> int:
    engine = _engine(args)
    callees = engine.get_callees(args.symbol)
    if args.json:
        _emit_json(callees)
        return 0 if callees else 1
    if not callees:
        print(f"No callees recorded for {args.symbol}.")
        return 1
    print(f"{len(callees)} callee(s) of {args.symbol}:")
    for c in callees:
        print(f"  {c}")
    return 0


def cmd_raises(args: argparse.Namespace) -> int:
    engine = _engine(args)
    raises = engine.get_raised_exceptions(args.symbol)
    if args.json:
        _emit_json(raises)
        return 0 if raises else 1
    if not raises:
        print(f"No raises recorded for {args.symbol}.")
        return 1
    print(f"{len(raises)} exception(s) raised by {args.symbol}:")
    for r in raises:
        print(f"  {r}")
    return 0


def cmd_card(args: argparse.Namespace) -> int:
    engine = _engine(args)
    card = engine.get_symbol_card(args.symbol)
    if card is None:
        if args.json:
            _emit_json(None)
        else:
            print(f"Symbol not found: {args.symbol}", file=sys.stderr)
        return 1
    if args.json:
        _emit_json(card)
        return 0
    name = card["name"]
    print(name)
    file_v = card.get("file")
    if file_v:
        suffix = ""
        if card.get("line"):
            suffix = f":{card['line']}"
            if card.get("end_line") and card["end_line"] != card["line"]:
                suffix += f"-{card['end_line']}"
        print(f"  file: {file_v}{suffix}")
    for k in ("kind", "params", "role"):
        if card.get(k):
            print(f"  {k}: {card[k]}")
    if card.get("doc"):
        print(f"  doc: {card['doc']}")
    callees = card.get("callees") or []
    if callees:
        print(
            f"  callees ({len(callees)}): {', '.join(callees[:8])}"
            + (" ..." if len(callees) > 8 else "")
        )
    raises = card.get("raises") or []
    if raises:
        print(f"  raises: {', '.join(raises)}")
    test = card.get("test") or {}
    if test.get("test_file"):
        line = test.get("line")
        suf = f":{line}" if line else ""
        print(f"  test: {test['test_file']}{suf}  ({test.get('test_function')})")
    callers = card.get("callers") or {}
    total = callers.get("total", 0)
    if total:
        print(f"  callers ({total}):")
        for c in (callers.get("top") or [])[:5]:
            print(f"    {c.get('file')}  [{c.get('kind') or 'file'}]")
    else:
        print("  callers: (none)")
    return 0


def cmd_file_card(args: argparse.Namespace) -> int:
    engine = _engine(args)
    card = engine.get_file_card(args.path)
    if card is None:
        if args.json:
            _emit_json(None)
        else:
            print(f"File not in any module map: {args.path}", file=sys.stderr)
        return 1
    if args.json:
        _emit_json(card)
        return 0
    print(card["file"])
    deps = card.get("dependencies") or []
    if deps:
        print(f"  dependencies: {', '.join(deps)}")
    roles = card.get("roles") or {}
    if roles:
        bits = ", ".join(f"{r}: {n}" for r, n in roles.items())
        print(f"  roles: {bits}")
    exports = card.get("exports") or []
    if exports:
        print(f"  exports ({len(exports)}):")
        for exp in exports:
            line = exp.get("line")
            suf = f":{line}" if line else ""
            tag = exp.get("kind") or ""
            role = f" [{exp['role']}]" if exp.get("role") else ""
            doc = f"  -- {exp['doc']}" if exp.get("doc") else ""
            print(f"    - {exp.get('name')}{suf}  {tag}{role}{doc}")
    return 0


def cmd_changed(args: argparse.Namespace) -> int:
    engine = _engine(args)
    out = engine.get_changed_symbols(base=args.base)
    if args.json:
        _emit_json(out)
        return 0 if out else 1
    if not out:
        print("No changed symbols (or not a git repo).")
        return 1
    print(f"{len(out)} changed symbol(s):")
    for r in out:
        line = r.get("line")
        end = r.get("end_line")
        suf = f":{line}" if line else ""
        if end and end != line:
            suf += f"-{end}"
        role = f" [{r['role']}]" if r.get("role") else ""
        print(f"  {r['name']}  {r.get('file')}{suf}  ({r.get('kind')}){role}")
    return 0


def cmd_decorated(args: argparse.Namespace) -> int:
    engine = _engine(args)
    out = engine.get_decorated_with(args.decorator)
    if args.json:
        _emit_json(out)
        return 0 if out else 1
    if not out:
        print(f"No symbols decorated with {args.decorator!r}.")
        return 1
    print(f"{len(out)} symbol(s) decorated with {args.decorator!r}:")
    for r in out:
        line = r.get("line")
        suf = f":{line}" if line else ""
        role = f" [{r['role']}]" if r.get("role") else ""
        print(f"  {r['name']}  {r.get('file')}{suf}  ({r.get('kind')}){role}")
    return 0


def cmd_repo_map(args: argparse.Namespace) -> int:
    engine = _engine(args)
    out = engine.repo_map()
    if args.json:
        _emit_json(out)
        return 0
    totals = out.get("totals") or {}
    print(
        f"=== {totals.get('modules', 0)} modules, "
        f"{totals.get('files', 0)} files, "
        f"{totals.get('exports', 0)} exports ==="
    )
    modules = out.get("modules") or []
    if not modules:
        return 0
    width = max(len(m["path"]) for m in modules)
    for m in modules:
        dom = f"  [{m['dominant_role']}]" if m.get("dominant_role") else ""
        print(f"  {m['path'].ljust(width)}  {m['files']:>3} files  {m['exports']:>3} exports{dom}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    engine = _engine(args)
    out = engine.get_session_metrics(
        since=args.since,
        group_by=args.by,
        quality=bool(getattr(args, "quality", False)),
    )
    if args.json:
        _emit_json(out)
        return 0
    calls = out.get("calls", 0)
    if not calls:
        print(f"No metrics for since={args.since!r}.")
        return 0
    print(
        f"=== since {args.since}: {calls} calls, "
        f"~{out.get('total_tokens', 0)} tok, "
        f"avg {out.get('avg_t_ms', 0)} ms, "
        f"empty {int(out.get('empty_ratio', 0) * 100)}%, "
        f"ok {int(out.get('ok_ratio', 1) * 100)}% ==="
    )
    bucket_key = f"by_{args.by}"
    buckets = out.get(bucket_key, {}) or {}
    if buckets:
        width = max(len(k) for k in buckets)
        # Sort by call count descending — top consumers first.
        rows = sorted(
            buckets.items(),
            key=lambda kv: kv[1].get("calls", 0),
            reverse=True,
        )
        for key, stats in rows:
            n = stats.get("calls", 0)
            pct = (100.0 * n / calls) if calls else 0.0
            toks = stats.get("tokens", 0)
            empty = int(stats.get("empty_ratio", 0) * 100)
            avg = stats.get("avg_t_ms", 0)
            print(
                f"  {key.ljust(width)}  {n:>3}  ({pct:>2.0f}%)  "
                f"~{toks} tok   avg {avg} ms   empty {empty}%"
            )

    quality = out.get("quality")
    if quality:
        print()
        print(f"--- quality: {quality.get('total_findings', 0)} finding(s) ---")
        for kind in ("wasteful_pairs", "hot_rereads", "empty_streaks"):
            findings = quality.get(kind) or []
            if not findings:
                continue
            print(f"  [{kind}] ({len(findings)})")
            for f in findings[:5]:
                sev = f.get("severity", "info").upper()
                print(f"    {sev}  {f.get('message')}")
            if len(findings) > 5:
                print(f"    ... +{len(findings) - 5} more")
    return 0


def cmd_slice(args: argparse.Namespace) -> int:
    engine = _engine(args)
    out = engine.read_slice(args.file, args.start, args.end)
    if out is None:
        if args.json:
            _emit_json(None)
        else:
            print(
                f"Could not read slice: {args.file}:{args.start}-{args.end}",
                file=sys.stderr,
            )
        return 1
    if args.json:
        _emit_json(out)
    else:
        marker = "  (truncated)" if out.get("truncated") else ""
        print(f"{out['file']}:{out['start']}-{out['end']}{marker}")
        for offset, line in enumerate(out["content"].splitlines()):
            print(f"  {out['start'] + offset:>5}  {line}")
    return 0


def cmd_calls(args: argparse.Namespace) -> int:
    engine = _engine(args)
    callers = engine.who_calls(args.symbol)
    if args.json:
        _emit_json(callers)
        return 0 if callers else 1
    _print_callers(args.symbol, callers)
    return 0 if callers else 1


def cmd_role(args: argparse.Namespace) -> int:
    engine = _engine(args)
    names = engine.find_by_role(args.role)
    if args.json:
        _emit_json(names)
        return 0 if names else 1
    _print_role(args.role, names)
    return 0 if names else 1


def cmd_module(args: argparse.Namespace) -> int:
    engine = _engine(args)
    summary = engine.summarise_module(args.path)
    if summary is None:
        if args.json:
            _emit_json(None)
        else:
            print(f"No module map at: {args.path}", file=sys.stderr)
        return 1
    if args.json:
        _emit_json(summary)
    else:
        _print_module(args.path, summary)
    return 0


def cmd_roles(args: argparse.Namespace) -> int:
    engine = _engine(args)
    counts = engine.list_roles()
    if args.json:
        _emit_json(counts)
    else:
        _print_roles(counts)
    return 0


def cmd_modules(args: argparse.Namespace) -> int:
    engine = _engine(args)
    modules = engine.list_modules()
    if args.json:
        _emit_json(modules)
    else:
        _print_modules(modules)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """Shell out to the builder to keep its existing behaviour intact."""
    builder = os.path.join(_HERE, "agent_map.py")
    cmd = [sys.executable, builder]
    proc = subprocess.run(cmd, cwd=args.root)
    return proc.returncode


# ----------------------------------------------------------------------
# Feature A — convention linter
# ----------------------------------------------------------------------


def _print_violations(violations: List[Dict[str, Any]]) -> None:
    if not violations:
        print("No violations.")
        return
    # Group by rule_id for readability.
    by_rule: Dict[str, List[Dict[str, Any]]] = {}
    for v in violations:
        by_rule.setdefault(v["rule_id"], []).append(v)
    for rule_id in sorted(by_rule):
        bucket = by_rule[rule_id]
        sev = bucket[0].get("severity", "warn")
        print(f"[{sev.upper()}] {rule_id}  ({len(bucket)} hit(s))")
        for v in bucket:
            print(f"  {v['file']}:{v['line']}  {v['message']}")


def cmd_lint(args: argparse.Namespace) -> int:
    engine = _engine(args)
    violations = engine.lint_violations()
    if args.json:
        _emit_json(violations)
    else:
        _print_violations(violations)
    # Exit 1 on any error-severity hit, 0 otherwise (warn/info don't
    # break CI by default).
    from conventions import has_error  # type: ignore[import-not-found]

    return 1 if has_error(violations) else 0


# ----------------------------------------------------------------------
# Feature B — test linking
# ----------------------------------------------------------------------


def cmd_test(args: argparse.Namespace) -> int:
    engine = _engine(args)
    entry = engine.find_test(args.symbol)
    if entry is None:
        if args.json:
            _emit_json(None)
        else:
            print(f"No test found for {args.symbol}.")
        return 1
    if args.json:
        _emit_json({"symbol": args.symbol, **entry})
    else:
        print(f"{args.symbol}")
        print(f"  test_file: {entry.get('test_file')}")
        print(f"  test_function: {entry.get('test_function')}")
        if entry.get("line"):
            print(f"  line: {entry['line']}")
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    engine = _engine(args)
    stats = engine.coverage_stats()
    if args.json:
        _emit_json(stats)
        return 0
    if not stats:
        print("No symbols indexed.")
        return 0
    width = max(len(k) for k in stats)
    for role in stats:
        bucket = stats[role]
        with_t = bucket["with_test"]
        total = bucket["total"]
        pct = (100.0 * with_t / total) if total else 0.0
        print(f"  {role.ljust(width)}  {with_t}/{total}  ({pct:.0f}%)")
    return 0


# ----------------------------------------------------------------------
# Feature C — route bridge
# ----------------------------------------------------------------------


def _print_route(entry: Dict[str, Any]) -> None:
    print(f"  path:    {entry.get('path')}")
    print(f"  method:  {entry.get('method')}")
    print(f"  handler: {entry.get('handler')}")
    print(f"  file:    {entry.get('file')}:{entry.get('line')}")
    callers = entry.get("callers_js") or []
    if callers:
        print(f"  callers_js: ({len(callers)})")
        for c in callers:
            print(f"    {c.get('file')}:{c.get('line')}  {c.get('raw')}")
    else:
        print("  callers_js: (none)")


def cmd_route(args: argparse.Namespace) -> int:
    engine = _engine(args)
    entry = engine.find_route(args.path)
    if entry is None:
        if args.json:
            _emit_json(None)
        else:
            print(f"No route found: {args.path}", file=sys.stderr)
        return 1
    if args.json:
        _emit_json(entry)
    else:
        _print_route(entry)
    return 0


def cmd_route_callers(args: argparse.Namespace) -> int:
    engine = _engine(args)
    callers = engine.route_callers(args.path)
    if args.json:
        _emit_json(callers)
        return 0 if callers else 1
    if not callers:
        print(f"No JS callers for {args.path}.")
        return 1
    print(f"{len(callers)} JS caller(s) for {args.path}:")
    for c in callers:
        print(f"  {c.get('file')}:{c.get('line')}  {c.get('raw')}")
    return 0


# ----------------------------------------------------------------------
# Argparse wiring
# ----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vc-context",
        description="Query the vc-context-builder artifacts without "
        "loading the JSON into your context window.",
    )
    parser.add_argument(
        "--root",
        default=os.getcwd(),
        help="Project root that contains agent_root.json (default: cwd).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON instead of a human-readable rendering.",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_find = sub.add_parser("find", help="Look up a symbol by name.")
    p_find.add_argument("symbol")
    p_find.add_argument(
        "--fields",
        help="Comma-separated whitelist (e.g. file,line,kind). "
        "Default = full record minus fact fields.",
    )
    p_find.add_argument(
        "--body",
        action="store_true",
        help="Embed verbatim source body in the response.",
    )
    p_find.set_defaults(handler=cmd_find)

    p_calls = sub.add_parser("calls", help="Best-effort callers of a symbol.")
    p_calls.add_argument("symbol")
    p_calls.set_defaults(handler=cmd_calls)

    p_callees = sub.add_parser(
        "callees",
        help="What identifiers does this symbol invoke? (AST-derived)",
    )
    p_callees.add_argument("symbol")
    p_callees.set_defaults(handler=cmd_callees)

    p_raises = sub.add_parser(
        "raises",
        help="What exception classes does this symbol raise?",
    )
    p_raises.add_argument("symbol")
    p_raises.set_defaults(handler=cmd_raises)

    p_slice = sub.add_parser(
        "slice",
        help="Read a bounded line range of a project file (≤200 lines).",
    )
    p_slice.add_argument("file")
    p_slice.add_argument("start", type=int)
    p_slice.add_argument("end", type=int)
    p_slice.set_defaults(handler=cmd_slice)

    p_card = sub.add_parser(
        "card",
        help="One-call symbol overview (find_symbol + callees + raises + test + callers).",
    )
    p_card.add_argument("symbol")
    p_card.set_defaults(handler=cmd_card)

    p_fcard = sub.add_parser(
        "file-card",
        help="One-call file overview (exports + deps + dominant role).",
    )
    p_fcard.add_argument("path")
    p_fcard.set_defaults(handler=cmd_file_card)

    p_changed = sub.add_parser(
        "changed",
        help="Symbols touched by `git diff <base>..HEAD` (default: working tree).",
    )
    p_changed.add_argument(
        "--base",
        default=None,
        help="Git ref to diff against (e.g. main, origin/main). Default: working tree.",
    )
    p_changed.set_defaults(handler=cmd_changed)

    p_dec = sub.add_parser(
        "decorated",
        help="Symbols whose decorators include the given name (suffix-aware).",
    )
    p_dec.add_argument("decorator")
    p_dec.set_defaults(handler=cmd_decorated)

    p_repo = sub.add_parser(
        "repo-map",
        help="Top-level project shape (modules + roles + counts).",
    )
    p_repo.set_defaults(handler=cmd_repo_map)

    p_stats = sub.add_parser(
        "stats",
        help="Aggregate per-call MCP telemetry (calls, tokens, latency).",
    )
    p_stats.add_argument(
        "--since",
        default="today",
        help="24h | 7d | today | all (default today).",
    )
    p_stats.add_argument(
        "--by",
        default="tool",
        choices=("tool", "hour", "empty"),
        help="Group buckets by (default tool).",
    )
    p_stats.add_argument(
        "--quality",
        action="store_true",
        help="Add Phase-2 quality findings (wasteful pairs, hot rereads, empty streaks).",
    )
    p_stats.set_defaults(handler=cmd_stats)

    p_role = sub.add_parser("role", help="All symbols tagged with a role.")
    p_role.add_argument("role")
    p_role.set_defaults(handler=cmd_role)

    p_module = sub.add_parser("module", help="Summarise a folder's module map.")
    p_module.add_argument("path")
    p_module.set_defaults(handler=cmd_module)

    p_roles = sub.add_parser("roles", help="role -> count map.")
    p_roles.set_defaults(handler=cmd_roles)

    p_modules = sub.add_parser("modules", help="List indexed module folders.")
    p_modules.set_defaults(handler=cmd_modules)

    p_build = sub.add_parser("build", help="Run the builder (agent_map.py).")
    p_build.set_defaults(handler=cmd_build)

    # Feature A — convention linter
    p_lint = sub.add_parser(
        "lint",
        help="Report convention violations from .vc-context/conventions.json.",
    )
    p_lint.set_defaults(handler=cmd_lint)

    # Feature B — test linking
    p_test = sub.add_parser(
        "test",
        help="Find the nearest test for a symbol.",
    )
    p_test.add_argument("symbol")
    p_test.set_defaults(handler=cmd_test)

    p_cov = sub.add_parser(
        "coverage",
        help="Print symbol-test linking ratio per role and overall.",
    )
    p_cov.set_defaults(handler=cmd_coverage)

    # Feature C — route bridge
    p_route = sub.add_parser(
        "route",
        help="Look up a backend route by URL path.",
    )
    p_route.add_argument("path")
    p_route.set_defaults(handler=cmd_route)

    p_rcal = sub.add_parser(
        "route-callers",
        help="List JS/TS call-sites that hit this route path.",
    )
    p_rcal.add_argument("path")
    p_rcal.set_defaults(handler=cmd_route_callers)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except FileNotFoundError as exc:
        print(f"vc-context: missing artifact — {exc}", file=sys.stderr)
        print("Hint: run `vc-context build` (or python3 .ai-context/agent_map.py)", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
