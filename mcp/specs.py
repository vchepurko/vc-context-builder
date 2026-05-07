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
                "{file, line, end_line, kind, params, doc, role} "
                "record, or null when the name is unknown. `line` and "
                "`end_line` are 1-indexed; `end_line` is Python-only "
                "(JS/TS only carries `line`).\n\n"
                "Token economy:\n"
                "- Pass `fields: ['file', 'line']` for a 'jump to X' "
                "answer (~40 tokens) — agent can Read(file, "
                "offset=line, limit=20) immediately, no follow-up grep.\n"
                "- Pass `include_body: true` to embed the function/class "
                "source in the response and skip the Read entirely.\n"
                "- For multiple symbols, prefer `find_symbols` (one "
                "round-trip vs N)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name (case-sensitive).",
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Whitelist of keys to keep in the response "
                            "(e.g. ['file'], ['file', 'kind']). Default "
                            "= full record."
                        ),
                    },
                    "include_body": {
                        "type": "boolean",
                        "description": (
                            "Embed verbatim source body (Python: AST "
                            "segment; JS/TS: line-based slice). "
                            "Saves a Read."
                        ),
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "find_symbols",
            "description": (
                "Batch lookup — N symbol records in one MCP call. "
                "Returns a {name → record_or_null} map. Same `fields` "
                "and `include_body` knobs as find_symbol.\n\n"
                "Token economy: 3 separate find_symbol calls cost "
                "~3 × 135 = ~400 tokens of round-trip overhead; one "
                "find_symbols(['A','B','C']) call carries the same "
                "payload at ~150 tokens."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Symbol names (case-sensitive).",
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Same as find_symbol.fields.",
                    },
                    "include_body": {
                        "type": "boolean",
                        "description": "Same as find_symbol.include_body.",
                    },
                },
                "required": ["names"],
            },
        },
        {
            "name": "get_callees",
            "description": (
                "Return identifiers this symbol calls in its body — "
                "the AST-derived 'what does X invoke?' axis "
                "complementing `who_calls` (which is 'who invokes X?'). "
                "Sorted, deduplicated. Bare ``foo()`` → ``foo``; "
                "attribute chains ``a.b.c()`` → rightmost attribute "
                "(``c``).\n\n"
                "Use as a fact-check before claiming 'this function "
                "depends on X' — saves reading the body."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Symbol name (case-sensitive).",
                    },
                },
                "required": ["symbol"],
            },
        },
        {
            "name": "get_raised_exceptions",
            "description": (
                "Return exception class names this symbol raises. "
                "``raise ValueError(...)`` → ``ValueError``; "
                "``raise pkg.HTTPError(...)`` → ``HTTPError``. Bare "
                "re-raise contributes nothing.\n\n"
                "Use to verify error-handling claims without reading "
                "the body. Empty list = no `raise` statements at AST "
                "level (helpers might still propagate; recurse with "
                "`get_callees` if needed)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Symbol name (case-sensitive).",
                    },
                },
                "required": ["symbol"],
            },
        },
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
            "name": "get_symbol_card",
            "description": (
                "One-call symbol overview — bundles ``find_symbol`` "
                "(file/line/end_line/kind/params/doc/role), "
                "``get_callees``, ``get_raised_exceptions``, "
                "``find_test``, and a capped callers summary "
                "(``{total, top: [first-5]}``) into a single "
                "round-trip.\n\n"
                "Token economy: replaces the 4-call sequence "
                "(find_symbol → get_callees → get_raised_exceptions → "
                "who_calls → find_test) with one ~250-token "
                "response. Use to bootstrap a playbook step that "
                "needs full context before deciding what to read."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Symbol name (case-sensitive).",
                    },
                },
                "required": ["symbol"],
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
            "name": "get_decorated_with",
            "description": (
                "Return symbols whose ``decorators`` array contains "
                "the given decorator name. Match is suffix-aware: "
                "``'router.get'`` matches ``router.get`` exactly; "
                "``'get'`` matches ``router.get`` / ``app.get`` / "
                "bare ``get``. Pass the full attribute path to "
                "disambiguate.\n\n"
                "Generalisation of ``find_by_role`` — works for ANY "
                "decorator the indexer captured, not just the "
                "role-mapped ones (``@cached``, ``@deprecated``, "
                "custom decorators). Each item: "
                "``{name, file, line, kind, role?}``, sorted by "
                "``(file, line)``."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "decorator": {
                        "type": "string",
                        "description": (
                            "Decorator name (with or without dotted "
                            "path)."
                        ),
                    },
                },
                "required": ["decorator"],
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

