"""Project-level MCP tool specs.

Everything that's not symbol-centric or Angular-specific: file / repo
/ git navigation, role + test queries, route / aiogram bridges,
i18n / locale lookups, lint / format / typecheck / mypy / ruff
inspectors, the whitelisted check runner, the notification audit log,
the per-call telemetry sidecar, and the Angular HTML template grep.
"""

from __future__ import annotations

from typing import Any, Dict, List


def specs() -> List[Dict[str, Any]]:
    return [
        {
            "name": "read_slice",
            "description": (
                "Read a small line range of a project file (1-indexed, "
                "inclusive). Capped at ``SLICE_MAX_LINES`` (200) and "
                "``SLICE_MAX_BYTES`` (8000); ``truncated: true`` flags "
                "either limit firing.\n\n"
                "Pair with `find_symbol(..., fields=['file','line',"
                "'end_line'])` for evidence-citation patterns: agent "
                "asserts a fact, then reads exactly the line range "
                "that proves it. Path is resolved against "
                "``project_root`` and rejected if it escapes the tree."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Project-relative file path.",
                    },
                    "start": {
                        "type": "integer",
                        "description": "First line (1-indexed, inclusive).",
                    },
                    "end": {
                        "type": "integer",
                        "description": "Last line (1-indexed, inclusive).",
                    },
                },
                "required": ["file", "start", "end"],
            },
        },
        {
            "name": "get_file_card",
            "description": (
                "One-call file overview — exports + dependencies + "
                "dominant role of a single file. Slim version of "
                "``summarise_module`` scoped to one path.\n\n"
                "Use to answer 'what does this file do?' without "
                "reading it. Returns null when the file isn't in any "
                "module map."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project-relative file path.",
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "get_changed_symbols",
            "description": (
                "Return symbols whose ``(file, line, end_line)`` "
                "overlap any hunk of ``git diff <base>..HEAD`` "
                "(default base: working tree). Each item: "
                "``{name, file, line, end_line, kind, role?}``, "
                "sorted by ``(file, line)``.\n\n"
                "Use to scope a refactor review or CI signal — "
                "'which symbols did this branch actually touch?' "
                "Empty list when not in a git repo OR diff is empty. "
                "Symbols not in the index (new files yet to re-index) "
                "are silently dropped."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "base": {
                        "type": "string",
                        "description": (
                            "Git ref to diff against "
                            "(e.g. 'main', 'origin/main'). "
                            "Default = working tree."
                        ),
                    },
                },
            },
        },
        {
            "name": "repo_map",
            "description": (
                "Top-level project shape — every module with file "
                "count, export count, dominant role, role histogram. "
                "Walks every ``_module_map.json`` once.\n\n"
                "Cheapest answer to 'what does this project look "
                "like?'. Use as the first call when starting fresh "
                "on an unfamiliar codebase. Output: "
                "``{modules: [...], totals: {modules, files, exports}}``."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "find_by_role",
            "description": (
                "Return every symbol name tagged with the given role "
                "(e.g. 'webhook', 'route', 'migration', "
                "'scheduler-job', 'repository', 'service', 'api-client', "
                "'aiogram-handler')."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                },
                "required": ["role"],
            },
        },
        {
            "name": "summarise_module",
            "description": (
                "Tight summary of a folder's _module_map.json: file "
                "names + each export's name/kind/role/first-line doc. "
                "Params are stripped — call find_symbol for signatures."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "Project-relative folder path.",
                    },
                },
                "required": ["folder"],
            },
        },
        {
            "name": "list_roles",
            "description": "Return a {role: count} map across the project.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "list_modules",
            "description": "Return every scanned module folder.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "lint_violations",
            "description": (
                "Run the convention linter and return all violations. "
                "Rules live in .vc-context/conventions.json at the parent "
                "project root. Empty list when the file is missing."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "find_test",
            "description": (
                "Return the nearest existing test for a symbol "
                "(test_file, test_function, line) or null. Reads "
                "agent_tests.json with a live-scan fallback."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        },
        {
            "name": "route_callers",
            "description": (
                "Return the JS/TS call-sites that hit a backend route "
                "path (e.g. '/api/foo' or 'GET /api/foo')."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "route_for_js_call",
            "description": (
                "Return every backend route whose callers_js list "
                "mentions the given JS/TS file path."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        },
        {
            "name": "find_callback",
            "description": (
                "Resolve an aiogram callback_data string (e.g. "
                "'adm:staff_add' or 'adm:staff_detail:42') to the "
                "handler(s) that listen for it. Tries an exact match "
                "first, then falls back to the longest startswith "
                "prefix in the index."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"data": {"type": "string"}},
                "required": ["data"],
            },
        },
        {
            "name": "trace_fsm_flow",
            "description": (
                "Trace an aiogram FSM state's lifecycle: where it's "
                "declared, which handlers ENTER it via state.set_state, "
                "and which handlers CONSUME it via decorator filter. "
                "Accepts 'AddStaffState.waiting_user_id' or just "
                "'waiting_user_id' when unambiguous."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"state": {"type": "string"}},
                "required": ["state"],
            },
        },
        {
            "name": "coverage_for_role",
            "description": (
                "Test-coverage view by role. Without 'role' — returns "
                "overall + per-role counts and percentages. With 'role' "
                "(any built-in or custom role, including legacy "
                "umbrellas like 'aiogram-handler') — returns "
                "{total, with_test, coverage_pct, missing, covered} "
                "where 'missing' lists symbols WITHOUT a linked test."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": ("Optional role name. Omit for whole-project summary."),
                    },
                },
            },
        },
        {
            "name": "classify_tests",
            "description": (
                "Categorise every test_*.py file as 'unit', "
                "'integration' (touches HTTP/DB/queue boundary OR "
                "carries pytest.mark.integration), or 'unknown'. "
                "Returns {summary: {category: count}, files: "
                "{path: {category, signals}}}. Use to find slow tests "
                "you can defer behind a marker."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "tests_by_category",
            "description": (
                "Return the list of test file paths for a given "
                "category ('unit' / 'integration' / 'unknown')."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"category": {"type": "string"}},
                "required": ["category"],
            },
        },
        {
            "name": "list_checks",
            "description": (
                "Return the names of whitelisted commands declared "
                "under .vc-context/conventions.json → 'checks'. Use "
                "before run_check to discover what's safe to invoke "
                "(e.g. 'test-unit', 'lint', 'typecheck')."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "run_check",
            "description": (
                "Execute a whitelisted check declared in "
                ".vc-context/conventions.json. Returns "
                "{returncode, duration_ms, stdout_tail, stderr_tail, "
                "summary, error?}. Unknown name → returncode -2; "
                "timeout → -1; spawn failure → -3. Use to run tests / "
                "lint / typecheck without exposing arbitrary shell."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "timeout_sec": {"type": "integer", "minimum": 1},
                },
                "required": ["name"],
            },
        },
        {
            "name": "get_session_metrics",
            "description": (
                "Aggregate this project's per-call MCP telemetry. "
                "Returns "
                "{calls, total_tokens, avg_t_ms, empty_ratio, ok_ratio, "
                "by_<group>, quality?}. Use to *see* how the agent is "
                "using the MCP surface — call counts, payload tokens, "
                "latency, and 'wasted' empty results.\n\n"
                "`since` accepts ``24h`` / ``7d`` / ``today`` / ``all`` "
                "(default ``today``). `group_by` is one of ``tool`` "
                "(default), ``hour``, ``empty``.\n\n"
                "Pass `quality: true` to add a `quality` block with "
                "wasteful round-trips, hot rereads, and empty streaks. "
                "Detectors are conservative; each finding cites "
                "evidence (timestamps + tool calls).\n\n"
                "Telemetry lives in "
                "``~/.vc-context/metrics/<repo-hash>-<date>.jsonl`` "
                "(override via ``VC_CONTEXT_METRICS_DIR``). The writer "
                "is fail-open — broken metrics never break a tool call."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "since": {
                        "type": "string",
                        "description": "24h | 7d | today | all (default today).",
                    },
                    "group_by": {
                        "type": "string",
                        "description": "tool | hour | empty (default tool).",
                    },
                    "quality": {
                        "type": "boolean",
                        "description": (
                            "Include Phase-2 quality findings "
                            "(wasteful_pairs, hot_rereads, empty_streaks)."
                        ),
                    },
                },
            },
        },
        {
            "name": "list_locale_keys",
            "description": (
                "Return all i18n keys (sorted), optionally filtered to "
                "one namespace (e.g. 'admin', 'common'). Reads "
                "agent_locale_keys.json, populated for projects with a "
                "locales/<lang>/<ns>.json layout. Empty list when no "
                "locale index is present."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": "Optional: namespace filter (the "
                        "JSON filename without .json).",
                    },
                },
            },
        },
        {
            "name": "find_locale_key",
            "description": (
                "Substring (case-insensitive) match across i18n keys. "
                "Use for 'every key starting with staff_' "
                "(pattern='staff_') or 'all email-related keys' "
                "(pattern='email'). Replaces grep across "
                "locales/*/*.json files."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
        {
            "name": "get_locale_key",
            "description": (
                "Full entry for one i18n key — {namespace, languages, "
                "values: {lang: text}, missing: [langs that own the "
                "namespace file but don't carry this key]}. The "
                "'missing' list is the parity audit hook: empty = "
                "fully translated, non-empty = ship-blocking gap."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                },
                "required": ["key"],
            },
        },
        {
            "name": "find_pattern_in_configs",
            "description": (
                "Substring or regex search across non-code config "
                "files (env, yaml, toml, Caddyfile, Dockerfile, "
                "GitHub Actions, *.conf, *.ini). Replaces 'grep -rn' "
                "for questions like 'where is GOOGLE_OAUTH_* "
                "referenced' or 'which compose file sets restart: "
                "unless-stopped'. Returns matches as a list of "
                "{file, line, kind, text}. Bounded by `limit` "
                "(default 200, max 2000). Case-insensitive by "
                "default; pass case_sensitive=true for exact match, "
                "use_regex=true to interpret pattern as Python regex. "
                "Use `list_config_kinds` to see available kinds; "
                "narrow with kinds=['env','caddy'] to skip noise."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "kinds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Whitelist of config kinds (e.g. ['env','caddy']). Omit = all kinds."
                        ),
                    },
                    "case_sensitive": {"type": "boolean", "default": False},
                    "use_regex": {"type": "boolean", "default": False},
                    "limit": {"type": "integer", "default": 200},
                },
                "required": ["pattern"],
            },
        },
        {
            "name": "list_config_kinds",
            "description": (
                "Enumerate known config kinds usable with "
                "`find_pattern_in_configs` — env / yaml / toml / "
                "ini / caddy / nginx / conf / json / dockerfile / "
                "github-actions. Useful for `--help`-style discovery."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "rebuild_index",
            "description": (
                "Run ``agent_map.py`` against the active project root "
                "and flush the engine's lazy-load caches so subsequent "
                "queries see fresh ``agent_*.json`` artifacts. Use "
                "after the agent edited source files and wants symbol/"
                "role queries to reflect the edits — replaces the "
                "manual ``python3 .ai-context/agent_map.py`` shell "
                "round-trip. Returns "
                "{ok, returncode, duration_ms, stderr_tail, stdout_tail}."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "notify_log_search",
            "description": (
                "Search the rotating notification audit log emitted "
                "by the project's services/notify pipeline. Returns "
                "matching records as a list of {ts, kind, "
                "recipient_uid, channel, outcome, keys}. AND-combines "
                "filters; empty filters return up to `limit` most-"
                "recent records. Projects without a logs/notify.jsonl "
                "return []. Use this instead of grep'ing log files "
                "for 'did kind X reach user Y?' questions."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "recipient": {"type": "integer"},
                    "channel": {"type": "string", "enum": ["telegram", "email"]},
                    "outcome": {
                        "type": "string",
                        "enum": ["sent", "failed", "skipped"],
                    },
                    "since": {
                        "type": "string",
                        "description": "Relative window like '7d' / '24h' or an "
                        "ISO date / datetime. None = no cutoff.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Cap response size (default 200) so MCP "
                        "client doesn't pull megabytes into context.",
                    },
                },
            },
        },
        {
            "name": "notify_log_stats",
            "description": (
                "Aggregate counters over the notification audit log: "
                "{total, by_kind: {kind: {sent, failed, skipped}}, "
                "by_channel: {channel: {sent, failed, skipped}}}. "
                "Optional 'since' (e.g. '7d') trims older records. "
                "Use for 'how is delivery health this week?' "
                "without scanning each record."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "since": {"type": "string"},
                },
            },
        },
        {
            "name": "ruff_violations",
            "description": (
                "Run `ruff check` in JSON mode and return a structured "
                "breakdown {total, by_code: {code: n}, by_file: {file: n}, "
                "violations?: [{file, line, end_line, code, message}]}. "
                "Use summary=true first to see the shape of failures "
                "without dumping the whole list, then drill in with "
                "code= / path_prefix=. limit caps the violations list "
                "(default 50) so MCP doesn't pull megabytes into context. "
                "Auto-skips on non-Python projects (no pyproject.toml / "
                "setup.py / *.py at root) — returns {total: 0, skipped: "
                "true, reason}. Override via "
                "conventions.json['ruff']['enabled'] = true/false."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Filter to one rule code (e.g. 'UP006').",
                    },
                    "path_prefix": {
                        "type": "string",
                        "description": "Project-relative startswith filter "
                        "(e.g. 'services/notify').",
                    },
                    "summary": {
                        "type": "boolean",
                        "description": "When true, drop the per-violation list "
                        "and return counts only.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Cap the violations list. 0 = no cap.",
                    },
                },
            },
        },
        {
            "name": "ruff_format",
            "description": (
                "Run `ruff format --check` and return {total, files?} "
                "— the list of files that would be reformatted, "
                "project-relative. Use summary=true for the cheapest "
                "possible 'is the codebase formatted?' check (~12 "
                "bytes when clean). path_prefix scopes to a subtree; "
                "limit caps the file list (default 50). Symmetric "
                "with ruff_violations: violations is for the linter, "
                "this is for the formatter. Auto-skips on non-Python "
                "projects (returns {total: 0, skipped: true, reason})."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path_prefix": {
                        "type": "string",
                        "description": "Project-relative startswith filter "
                        "(e.g. 'services/notify').",
                    },
                    "summary": {
                        "type": "boolean",
                        "description": "When true, drop the file list and return just {total}.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Cap the file list. 0 = no cap.",
                    },
                },
            },
        },
        {
            "name": "mypy_violations",
            "description": (
                "Run `mypy --output=json` and return a structured "
                "breakdown {total, by_code: {code: n}, by_file: "
                "{file: n}, violations?: [{file, line, end_line, "
                "code, severity, message}]}. Use summary=true first "
                "to see which error codes / files dominate without "
                "dumping the full list, then drill in with code= / "
                "path_prefix= / severity=. limit caps the violations "
                "list (default 50) so MCP doesn't pull megabytes "
                "into context. Auto-skips on non-Python projects or "
                "projects without mypy config ([tool.mypy] in "
                "pyproject.toml or mypy.ini) — returns {total: 0, "
                "skipped: true, reason}. Override via "
                "conventions.json['mypy']['enabled'] = true/false."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Filter to one error code "
                        "(e.g. 'union-attr', 'assignment').",
                    },
                    "path_prefix": {
                        "type": "string",
                        "description": "Project-relative startswith filter (e.g. 'bot/handlers').",
                    },
                    "severity": {
                        "type": "string",
                        "description": "Filter to severity: 'error', 'note', or 'warning'.",
                    },
                    "summary": {
                        "type": "boolean",
                        "description": "When true, drop the per-violation "
                        "list and return counts only.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Cap the violations list. 0 = no cap.",
                    },
                },
            },
        },
        {
            "name": "find_in_templates",
            "description": (
                "Search Angular HTML templates for a pattern (CSS class, "
                "binding expression, event handler, selector tag, etc.). "
                "Returns [{file, line, text}] matches, capped at 100. "
                "Case-insensitive. Use match_path to scope to a folder, "
                "e.g. 'src/app/modules/collection-player-v2/**'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Substring to search for in .html files.",
                    },
                    "match_path": {
                        "type": "string",
                        "description": "Optional fnmatch glob to filter files by relative path.",
                    },
                },
                "required": ["pattern"],
            },
        },
    ]
