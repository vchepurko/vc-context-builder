"""Subcommand handlers for the ``vc-context`` CLI.

Each ``cmd_*`` function takes the parsed ``argparse.Namespace`` and
returns the process exit code (0 = success, 1 = lookup miss, etc.).
Output is delegated to ``cli_renderers`` for the human-readable shape
and to ``_emit_json`` when ``--json`` is set.

Wired into argparse subparsers in :mod:`cli`. Adding a new
subcommand:

1. Add ``cmd_<name>(args)`` here.
2. Register a subparser in ``cli._build_parser`` with
   ``set_defaults(handler=cmd_<name>)``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from cli_renderers import (
    _emit_json,
    _print_callers,
    _print_module,
    _print_modules,
    _print_role,
    _print_roles,
    _print_route,
    _print_symbol,
    _print_violations,
)
from query_engine import QueryEngine

# Re-exported so cli.py can resolve `python3 cli.py build` to the
# adjacent ``agent_map.py`` regardless of cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))


def _engine(args: argparse.Namespace) -> QueryEngine:
    return QueryEngine(args.root)


# ----------------------------------------------------------------------
# Symbol-centric subcommands
# ----------------------------------------------------------------------


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


def cmd_verify(args: argparse.Namespace) -> int:
    engine = _engine(args)
    out = engine.verify(args.kind, args.subject, target=args.target)
    if args.json:
        _emit_json(out)
        return 0 if out.get("result") else 1
    mark = "✓" if out.get("result") else "✗"
    target_part = f" {args.target!r}" if args.target else ""
    print(f"{mark} verify({args.kind!r}, {args.subject!r}{target_part})")
    print(f"  evidence: {out.get('evidence', '')}")
    return 0 if out.get("result") else 1


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


def cmd_calls(args: argparse.Namespace) -> int:
    engine = _engine(args)
    callers = engine.who_calls(args.symbol)
    if args.json:
        _emit_json(callers)
        return 0 if callers else 1
    _print_callers(args.symbol, callers)
    return 0 if callers else 1


# ----------------------------------------------------------------------
# Project-level subcommands
# ----------------------------------------------------------------------


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
# Project initialisation
# ----------------------------------------------------------------------

# Groups advertised by mcp/server.py _TOOL_GROUPS — keep in sync.
_ALL_GROUPS = ["angular", "locale", "fsm", "notify_log", "route", "docs"]

# Which tool groups to disable for each detected project stack.
_STACK_DISABLED: dict = {
    "django": ["angular", "locale", "fsm", "notify_log", "route"],
    "fastapi": ["angular", "locale", "fsm", "notify_log", "route"],
    "flask": ["angular", "locale", "fsm", "notify_log", "route"],
    "angular": ["fsm", "notify_log", "locale"],
    "bot": ["angular", "route"],  # keep fsm — bots use state machines
    "generic": ["fsm", "notify_log"],
}


def _detect_stack(root: str) -> str:
    """Return a stack key based on files present in *root*."""
    req = os.path.join(root, "requirements.txt")
    pyproject = os.path.join(root, "pyproject.toml")
    pkg = os.path.join(root, "package.json")

    def _py_deps() -> str:
        for path in (req, pyproject):
            try:
                with open(path) as fh:
                    text = fh.read().lower()
                if "django" in text:
                    return "django"
                if "fastapi" in text:
                    return "fastapi"
                if "flask" in text:
                    return "flask"
                if "aiogram" in text or "telebot" in text or "pyrogram" in text:
                    return "bot"
            except FileNotFoundError:
                pass
        return ""

    if os.path.exists(req) or os.path.exists(pyproject):
        stack = _py_deps()
        if stack:
            return stack
        return "generic"

    if os.path.exists(pkg):
        try:
            with open(pkg) as fh:
                data = json.load(fh)
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "@angular/core" in deps:
                return "angular"
        except (json.JSONDecodeError, OSError):
            pass
        return "generic"

    return "generic"


def cmd_init(args: argparse.Namespace) -> int:
    """Generate or update .vc-context/conventions.json for a project.

    Idempotent by default: merges into an existing file, only adding keys
    that are absent.  Use --force to overwrite disabled_tool_groups even
    when it is already set.
    """
    root = args.root
    vc_dir = os.path.join(root, ".vc-context")
    out_path = os.path.join(vc_dir, "conventions.json")

    stack = _detect_stack(root)
    disabled = _STACK_DISABLED.get(stack, _STACK_DISABLED["generic"])
    force = getattr(args, "force", False)

    # Read existing config so we can merge instead of clobber.
    existing: dict = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as fh:
                existing = json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass

    added: list = []
    if "disabled_tool_groups" not in existing or force:
        existing["disabled_tool_groups"] = disabled
        added.append("disabled_tool_groups")
    for key, stub in (("ignore_dirs", []), ("checks", {}), ("rules", [])):
        if key not in existing:
            existing[key] = stub
            added.append(key)

    is_new = not os.path.exists(out_path)
    os.makedirs(vc_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=2)
        fh.write("\n")

    if is_new:
        action = "created"
    elif added:
        action = "updated"
    else:
        action = "no changes"
    print(f"vc-context init: {action} — {out_path}")
    print(f"  stack detected : {stack}")
    if added:
        print(f"  keys added     : {added}")
    else:
        print("  all keys already present; use --force to reset disabled_tool_groups")
    print(f"  available groups (all): {_ALL_GROUPS}")
    return 0


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


def cmd_ng_ajs_find(args: argparse.Namespace) -> int:
    """Find an AngularJS symbol in app/ by registration name."""
    engine = _engine(args)
    result = engine.ng_ajs_find(args.name)
    if args.json:
        _emit_json(result)
        return 0 if result else 1
    if not result:
        print(f"AJS symbol not found: {args.name!r}", file=sys.stderr)
        return 1
    print(f"{result['kind']}  {result['name']}")
    print(f"  {result['file']}:{result['line']}")
    return 0


def cmd_ng_module_members(args: argparse.Namespace) -> int:
    """List declarations/imports/exports/providers of an NgModule."""
    engine = _engine(args)
    result = engine.ng_module_members(args.name)
    if args.json:
        _emit_json(result)
        return 0 if result else 1
    if not result:
        print(f"NgModule not found: {args.name!r}", file=sys.stderr)
        return 1
    print(f"NgModule  {result['module']}")
    print(f"  {result['file']}")
    for section in ("declarations", "imports", "exports", "providers"):
        items = result.get(section) or []
        if items:
            print(f"\n  {section} ({len(items)}):")
            for item in items:
                print(f"    {item}")
    return 0


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


def cmd_backup(args: argparse.Namespace) -> int:
    """Back up project settings (AGENTS.md, CLAUDE.md, playbooks, conventions) to a ZIP."""
    from backup_manager import backup, preview_backup

    root = os.path.abspath(args.root)
    out = getattr(args, "out", None)

    if getattr(args, "preview", False):
        result = preview_backup(root)
        if args.json:
            _emit_json(result)
        else:
            print(f"Would back up {result['file_count']} file(s) ({result['total_bytes']} bytes):")
            for f in result["files"]:
                print(f"  {f['path']}  ({f['size_bytes']} B)")
        return 0

    result = backup(root, out_path=out)
    if args.json:
        _emit_json(result)
    else:
        n = len(result["files"])
        kb = result["size_bytes"] // 1024
        print(f"Backup created: {result['path']}  ({n} files, {kb} KB)")
        for f in result["files"]:
            print(f"  {f['path']}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore project settings from a ZIP backup."""
    from backup_manager import restore

    root = os.path.abspath(args.root)
    dry_run = bool(getattr(args, "dry_run", False))
    force = bool(getattr(args, "force", False))

    result = restore(args.backup_file, root, dry_run=dry_run, force=force)
    if args.json:
        _emit_json(result)
        return 0

    prefix = "[DRY RUN] " if dry_run else ""
    if result["restored"]:
        print(f"{prefix}Restored {len(result['restored'])} file(s):")
        for p in result["restored"]:
            print(f"  + {p}")
    if result["conflicts"] and not force:
        print(f"\n{prefix}{len(result['conflicts'])} conflict(s) skipped (use --force to overwrite):")
        for p in result["conflicts"]:
            print(f"  ! {p}")
    if not result["restored"] and not result["conflicts"]:
        print("Nothing to restore.")
    return 0


def cmd_backup_inspect(args: argparse.Namespace) -> int:
    """Show contents of an existing backup ZIP without extracting."""
    from backup_manager import inspect_backup

    result = inspect_backup(args.backup_file)
    if args.json:
        _emit_json(result)
        return 0

    m = result.get("manifest") or {}
    created = m.get("created_at", "unknown")
    project = m.get("project_name", "unknown")
    version = m.get("vc_context_version", "?")
    kb = result["size_bytes"] // 1024
    print(f"Backup: {result['path']}  ({kb} KB)")
    print(f"  project : {project}")
    print(f"  created : {created}")
    print(f"  version : {version}")
    print(f"  files   : {len(result['files'])}")
    for f in result["files"]:
        print(f"    {f['path']}  ({f['size_bytes']} B)")
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
