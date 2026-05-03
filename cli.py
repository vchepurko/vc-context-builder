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
