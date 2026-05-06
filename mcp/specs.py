"""JSON-Schema descriptors for every MCP tool surfaced over the wire.

Single ``tool_specs()`` function returning the full list. Pure data —
no dispatch, no engine references — so the spec file can be inspected /
diffed / snapshot-tested in isolation. Adding a tool: append a record
here AND wire a handler in ``mcp.dispatcher`` (the parity test in
``tests/test_mcp_server.py`` enforces both halves stay in sync).
"""

from __future__ import annotations

from typing import Any, Dict, List


def tool_specs() -> List[Dict[str, Any]]:
    """JSON-Schema descriptors for the six exposed tools."""
    return [
        {
            "name": "find_symbol",
            "description": (
                "Look up a symbol in agent_symbols.json. Returns the "
                "{file, kind, params, doc, role} record, or null when "
                "the name is unknown."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name (case-sensitive).",
                    },
                },
                "required": ["name"],
            },
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
            "name": "who_calls",
            "description": (
                "Best-effort reverse-dependency lookup: return files "
                "that import the package containing this symbol or "
                "list the symbol name in their dependencies. Heuristic, "
                "not a true call graph — confirm by reading the source."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                },
                "required": ["symbol"],
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
                        "description": (
                            "Optional role name. Omit for whole-project "
                            "summary."
                        ),
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
            "name": "find_call_sites",
            "description": (
                "Reverse call-site lookup. Return every Call(...) site "
                "in the project whose target matches a given callable. "
                "Accepts a plain name ('foo') or dotted path ('x.y'). "
                "Optional match_path is an fnmatch-style glob "
                "('services/**', 'bot/handlers/*.py'). Use to find who "
                "calls state.clear / session.commit / cache.delete / etc."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "callable": {"type": "string"},
                    "match_path": {"type": "string"},
                },
                "required": ["callable"],
            },
        },
        {
            "name": "logline_to_symbol",
            "description": (
                "Parse a Python logging line ('YYYY-MM-DD HH:MM:SS "
                "[LEVEL] dotted.logger: message') into "
                "{level, logger, file, message, symbol?, symbol_file?, "
                "role?}. Maps the dotted logger name to the project "
                "file via __name__-convention; if the message starts "
                "with a known symbol, folds in its file/role too."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"line": {"type": "string"}},
                "required": ["line"],
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
            "name": "inspect_class",
            "description": (
                "Return a structured summary of a Python class — "
                "{file, line, doc, bases, fields, methods}. Looks up "
                "the symbol in agent_symbols.json, then AST-walks the "
                "file. Works for SQLAlchemy models, pydantic schemas, "
                "dataclasses, plain classes. Use instead of `grep` for "
                "'what columns does Admin have?'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
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
                        "description": "When true, drop the file list and "
                                       "return just {total}.",
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
                        "description": "Project-relative startswith filter "
                                       "(e.g. 'bot/handlers').",
                    },
                    "severity": {
                        "type": "string",
                        "description": "Filter to severity: 'error', "
                                       "'note', or 'warning'.",
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
        {
            "name": "ng_audit_component",
            "description": (
                "Composite audit of a single Angular @Component. Returns "
                "{name, file, role, selector, template_url, standalone, "
                "inputs, outputs, style_urls, test} — everything indexed "
                "for the class without making the caller stitch four "
                "separate find_symbol / find_test / find_in_templates "
                "calls. Use this before any component-level refactor."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Component class name (case-sensitive).",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "ng_uses_selector",
            "description": (
                "Find every HTML template that uses an Angular selector "
                "(e.g. 'app-cart-item' or 'mat-button'). Wraps "
                "find_in_templates with two passes — `<selector` for "
                "elements and `[selector]` for attribute directives — "
                "deduped by (file, line). Returns [{file, line, text}] "
                "capped at 100."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Selector without surrounding brackets.",
                    },
                    "match_path": {
                        "type": "string",
                        "description": "Optional fnmatch glob to scope.",
                    },
                },
                "required": ["selector"],
            },
        },
        {
            "name": "ng_overview",
            "description": (
                "One-call zero-arg snapshot of Angular surface: counts "
                "for ng-component / ng-service / ng-module / ng-pipe / "
                "ng-directive / ng-guard, plus a `standalone_components` "
                "count and the list of detected `providers_root` "
                "(services with `providedIn: 'root'`). Cheap — reads "
                "agent_symbols.json once."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "ng_inject_graph",
            "description": (
                "For an Angular @Injectable service, list call sites "
                "across components / services / guards (heuristic: "
                "constructor params and `inject(Service)` calls in "
                "scrubbed bodies). Returns [{file, line, kind}] where "
                "kind is 'constructor' or 'inject'. Confirm by reading "
                "the source — this is a substring scan, not a full TS "
                "type-resolver."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": "Service class name.",
                    },
                },
                "required": ["service"],
            },
        },
        {
            "name": "ng_list_routes",
            "description": (
                "Every Angular route extracted from "
                "RouterModule.forRoot/forChild/provideRouter blocks. "
                "Each record: {path, component, file, line, lazy, "
                "redirect_to, guards}. Empty on non-Angular projects."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "ng_route_for_path",
            "description": (
                "Resolve an Angular URL path (e.g. 'users/:id' or "
                "'/admin') to its route record(s). Exact match first, "
                "falls back to substring so 'users' finds both "
                "'users/:id' and 'admin/users'. Strips a leading "
                "slash so either form works."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "ng_routes_for_component",
            "description": (
                "Reverse lookup — every Angular route whose "
                "`component` field equals the given class name. "
                "Useful for 'where is HomeComponent mounted?' before "
                "renaming or moving the file."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    ]

