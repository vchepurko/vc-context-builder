"""Symbol-centric MCP tool specs.

Everything that takes (or returns) a symbol name as the primary
identifier — find / cards / callees / raises / decorators / call-sites
/ class introspection / log-line back-resolution.
"""

from __future__ import annotations

from typing import Any, Dict, List


def specs() -> List[Dict[str, Any]]:
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
            "name": "verify",
            "description": (
                "Typed fact-check primitive — answer one yes/no claim "
                "about the symbol index without reading source.\n\n"
                "Kinds:\n"
                "- `exists` (subject) → does the symbol exist?\n"
                "- `calls` (subject, target) → does subject call target?\n"
                "- `decorated` (subject, target) → is subject decorated "
                "with target? (suffix-aware: 'post' matches 'app.post')\n"
                "- `raises` (subject, target) → does subject raise target "
                "(exception class name)?\n\n"
                "Returns `{kind, subject, target?, result: bool, "
                "evidence: str}`. Use as a one-call alternative to "
                "`find_symbol` + `get_callees` + `in` checks scattered "
                "across the agent's reasoning chain — the evidence "
                "string is short enough to quote in the answer."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["exists", "calls", "decorated", "raises"],
                    },
                    "subject": {
                        "type": "string",
                        "description": "Symbol name (case-sensitive).",
                    },
                    "target": {
                        "type": "string",
                        "description": (
                            "Target name. Required for calls/decorated/raises; ignored for exists."
                        ),
                    },
                },
                "required": ["kind", "subject"],
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
                        "description": ("Decorator name (with or without dotted path)."),
                    },
                },
                "required": ["decorator"],
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
    ]
