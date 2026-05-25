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

Implementation is split across three sibling modules:

* :mod:`cli_renderers` — pure presentation helpers (``_print_*``).
* :mod:`cli_handlers`  — every ``cmd_*`` subcommand handler.
* this file            — argparse wiring + ``main`` entrypoint.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

# Make sibling imports work whether invoked as
# ``python3 .ai-context/cli.py ...`` or via the bin wrapper.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from cli_handlers import (
    cmd_backup,
    cmd_backup_inspect,
    cmd_build,
    cmd_callees,
    cmd_calls,
    cmd_card,
    cmd_changed,
    cmd_coverage,
    cmd_decorated,
    cmd_file_card,
    cmd_find,
    cmd_init,
    cmd_lint,
    cmd_module,
    cmd_modules,
    cmd_ng_ajs_find,
    cmd_ng_module_members,
    cmd_raises,
    cmd_recall_experience,
    cmd_remember_experience,
    cmd_repo_map,
    cmd_restore,
    cmd_role,
    cmd_roles,
    cmd_route,
    cmd_route_callers,
    cmd_semantic,
    cmd_slice,
    cmd_stats,
    cmd_test,
    cmd_verify,
)

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

    # Symbol-centric subcommands ------------------------------------
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

    p_sem = sub.add_parser(
        "semantic-search",
        aliases=("semantic", "semantic_search"),
        help="Find symbols by concept rather than exact name.",
    )
    p_sem.add_argument("query")
    p_sem.add_argument("--top-k", type=int, default=5, help="Maximum hits to return.")
    p_sem.add_argument("--kind", default=None, help="Optional exact kind filter.")
    p_sem.add_argument("--role", default=None, help="Optional exact role filter.")
    p_sem.add_argument(
        "--include-tests",
        action="store_true",
        help="Include symbols from tests/ in search results.",
    )
    p_sem.set_defaults(handler=cmd_semantic)

    p_rem = sub.add_parser(
        "remember-experience",
        aliases=("remember_experience",),
        help="Store a repo-local decision, mistake, dead end, or pattern.",
    )
    p_rem.add_argument("--context", required=True, help="Situation where this applies.")
    p_rem.add_argument("--content", required=True, help="What to remember.")
    p_rem.add_argument(
        "--type",
        default="decision",
        choices=("decision", "mistake", "dead_end", "pattern"),
    )
    p_rem.add_argument("--source", default="user", choices=("user", "agent", "auto"))
    p_rem.add_argument("--source-file", default=None, help="Optional project-relative file.")
    p_rem.add_argument("--confidence", type=float, default=None, help="Optional 0..1 confidence.")
    p_rem.set_defaults(handler=cmd_remember_experience)

    p_rec = sub.add_parser(
        "recall-experience",
        aliases=("recall_experience",),
        help="Recall repo-local decisions, mistakes, dead ends, or patterns.",
    )
    p_rec.add_argument("context")
    p_rec.add_argument("--top-k", type=int, default=3, help="Maximum hits to return.")
    p_rec.add_argument(
        "--type",
        default=None,
        choices=("decision", "mistake", "dead_end", "pattern"),
    )
    p_rec.add_argument("--min-score", type=float, default=0.05)
    p_rec.set_defaults(handler=cmd_recall_experience)

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

    p_card = sub.add_parser(
        "card",
        help="One-call symbol overview (find_symbol + callees + raises + test + callers).",
    )
    p_card.add_argument("symbol")
    p_card.set_defaults(handler=cmd_card)

    p_dec = sub.add_parser(
        "decorated",
        help="Symbols whose decorators include the given name (suffix-aware).",
    )
    p_dec.add_argument("decorator")
    p_dec.set_defaults(handler=cmd_decorated)

    p_verify = sub.add_parser(
        "verify",
        help="Typed yes/no fact-check (exists | calls | decorated | raises).",
    )
    p_verify.add_argument("kind", choices=("exists", "calls", "decorated", "raises"))
    p_verify.add_argument("subject")
    p_verify.add_argument("target", nargs="?", default=None)
    p_verify.set_defaults(handler=cmd_verify)

    # Project / file / git subcommands ------------------------------
    p_slice = sub.add_parser(
        "slice",
        help="Read a bounded line range of a project file (≤200 lines).",
    )
    p_slice.add_argument("file")
    p_slice.add_argument("start", type=int)
    p_slice.add_argument("end", type=int)
    p_slice.set_defaults(handler=cmd_slice)

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

    p_repo = sub.add_parser(
        "repo-map",
        help="Top-level project shape (modules + roles + counts).",
    )
    p_repo.set_defaults(handler=cmd_repo_map)

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

    p_init = sub.add_parser(
        "init",
        help="Generate .vc-context/conventions.json for a new project.",
    )
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing conventions.json.",
    )
    p_init.set_defaults(handler=cmd_init)

    # Quality / lint / coverage / route / test subcommands ----------
    p_lint = sub.add_parser(
        "lint",
        help="Report convention violations from .vc-context/conventions.json.",
    )
    p_lint.set_defaults(handler=cmd_lint)

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

    p_ajs = sub.add_parser(
        "ng-ajs-find",
        help="Find an AngularJS symbol (.component/.service/.directive) in app/ by name.",
    )
    p_ajs.add_argument("name", help="AJS registration name (e.g. userProfileMenu).")
    p_ajs.set_defaults(handler=cmd_ng_ajs_find)

    p_ngmod = sub.add_parser(
        "ng-module-members",
        help="List declarations/imports/exports/providers of an NgModule.",
    )
    p_ngmod.add_argument("name", help="NgModule class name (e.g. MyProfileModule).")
    p_ngmod.set_defaults(handler=cmd_ng_module_members)

    # Backup / restore subcommands ---------------------------------
    p_bkp = sub.add_parser(
        "backup",
        help="Back up project settings (AGENTS.md, CLAUDE.md, playbooks, conventions) to a ZIP.",
    )
    p_bkp.add_argument(
        "--out",
        default=None,
        help="Output path for the ZIP file. Default: <project>/vc-context-backup-<name>-<ts>.zip",
    )
    p_bkp.add_argument(
        "--preview",
        action="store_true",
        help="Show what would be backed up without writing anything.",
    )
    p_bkp.set_defaults(handler=cmd_backup)

    p_rst = sub.add_parser(
        "restore",
        help="Restore project settings from a ZIP backup.",
    )
    p_rst.add_argument("backup_file", help="Path to the backup ZIP file.")
    p_rst.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be restored without writing anything.",
    )
    p_rst.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files (default: skip conflicts).",
    )
    p_rst.set_defaults(handler=cmd_restore)

    p_bkp_inspect = sub.add_parser(
        "backup-inspect",
        help="Show contents of an existing backup ZIP without extracting.",
    )
    p_bkp_inspect.add_argument("backup_file", help="Path to the backup ZIP file.")
    p_bkp_inspect.set_defaults(handler=cmd_backup_inspect)

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

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        # argparse types `args.handler` as `Any`; cast keeps mypy happy.
        return int(args.handler(args))
    except FileNotFoundError as exc:
        print(f"vc-context: missing artifact — {exc}", file=sys.stderr)
        print("Hint: run `vc-context build` (or python3 .ai-context/agent_map.py)", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
