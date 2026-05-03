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

from query_engine import QueryEngine  # noqa: E402


# ----------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------

def _emit_json(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, sort_keys=False)
    sys.stdout.write("\n")


def _print_symbol(name: str, entry: Dict[str, Any]) -> None:
    print(f"{name}")
    for key in ("file", "kind", "params", "role"):
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
    entry = engine.find_symbol(args.symbol)
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
        "--root", default=os.getcwd(),
        help="Project root that contains agent_root.json (default: cwd).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit raw JSON instead of a human-readable rendering.",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_find = sub.add_parser("find", help="Look up a symbol by name.")
    p_find.add_argument("symbol")
    p_find.set_defaults(handler=cmd_find)

    p_calls = sub.add_parser("calls", help="Best-effort callers of a symbol.")
    p_calls.add_argument("symbol")
    p_calls.set_defaults(handler=cmd_calls)

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
        print("Hint: run `vc-context build` (or python3 .ai-context/agent_map.py)",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
