"""Markdown docs MCP tool specs.

Surfaces the markdown index (``agent_docs_index.json``) over the
wire: section TOC, section lookup, doc enumeration, full-text
xref, link graph. Closes the navigation gap surfaced in submodule
ROADMAP under "Markdown / docs navigation".
"""

from __future__ import annotations

from typing import Any, Dict, List


def specs() -> List[Dict[str, Any]]:
    return [
        {
            "name": "get_doc_toc",
            "description": (
                "Return the section list for one ``.md`` file — "
                "[{level, text, line, end_line, anchor}, ...]. Replaces "
                "the grep '^## ' + Read combo for 'show me the layout "
                "of OPS.md'. Returns null when the file isn't in the "
                "index (typo / outside tracked tree).\n\n"
                "``max_level`` (1–6, default unlimited) trims the TOC "
                "to top-level entries — pass ``max_level: 2`` to get "
                "just the ``##`` headings on long docs like "
                "``IDEAS.md`` (~3 KB instead of the full ~12 KB tree). "
                "Out-of-range values are silently ignored (full TOC "
                "returned) — keeps the optional parameter forgiving."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": (
                            "Repo-relative path to the ``.md`` file (e.g. 'docs/OPS.md')."
                        ),
                    },
                    "max_level": {
                        "type": "integer",
                        "description": (
                            "Optional 1–6 cap. Drops headings deeper "
                            "than this level (e.g. ``2`` hides all "
                            "``###``+ subsections)."
                        ),
                    },
                },
                "required": ["file"],
            },
        },
        {
            "name": "find_doc_section",
            "description": (
                "Locate one section in ``file`` and return "
                "{level, text, line, end_line, anchor}. Pair with "
                "``read_slice(file, start_line, end_line)`` for a "
                "surgical read of just that section.\n\n"
                "Four selectors (priority: anchor > number > heading "
                "> header_pattern):\n"
                '  * ``anchor: "31"`` — slug prefix match (finds '
                "``31-unified-user``).\n"
                "  * ``number: 31`` — numeric heading prefix "
                "(``## 31. Something``).\n"
                '  * ``heading: "Unified User"`` — case-insensitive '
                "substring on heading text, ranked shortest-first.\n"
                '  * ``header_pattern: "..."`` — back-compat path; '
                "case-insensitive substring (or exact equality when "
                "``fuzzy: false``).\n\n"
                "Replaces the Bash 'grep -n ^## Foo && Read offset N "
                "limit M' two-step."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "anchor": {
                        "type": "string",
                        "description": (
                            "Section slug prefix (case-insensitive). "
                            "E.g. '31' → matches '31-unified-user'."
                        ),
                    },
                    "number": {
                        "type": "integer",
                        "description": ("Numeric heading prefix. E.g. 31 finds '## 31. ...'."),
                    },
                    "heading": {
                        "type": "string",
                        "description": (
                            "Case-insensitive substring on heading text. "
                            "Ranked shortest-heading-first."
                        ),
                    },
                    "header_pattern": {
                        "type": "string",
                        "description": (
                            "Back-compat substring (or exact text if "
                            "``fuzzy: false``) of the heading to match."
                        ),
                    },
                    "fuzzy": {
                        "type": "boolean",
                        "description": (
                            "Default true — case-insensitive substring "
                            "for ``header_pattern``. Set false for exact "
                            "heading-text equality."
                        ),
                    },
                },
                "required": ["file"],
            },
        },
        {
            "name": "list_docs",
            "description": (
                "Enumerate every indexed ``.md`` file with metadata "
                "(top_header, section_count, size_bytes). Use for "
                "'what docs do we have under docs/' (pass "
                "``path_prefix: 'docs/'``) before drilling into one "
                "with ``get_doc_toc``."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path_prefix": {
                        "type": "string",
                        "description": (
                            "Optional repo-relative prefix filter (e.g. 'docs/', 'changelog/')."
                        ),
                    },
                },
            },
        },
        {
            "name": "find_doc_xref",
            "description": (
                "Full-text search across all indexed markdown files. "
                "Returns [{file, line, snippet}] capped at "
                "``max_results``. Scans live file contents (not the "
                "stale index), so always returns fresh hits. Use for "
                "'where do we document <env var> / <concept>'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Default false.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Cap (default 50).",
                    },
                },
                "required": ["term"],
            },
        },
        {
            "name": "docs_link_graph",
            "description": (
                "Forward link adjacency for every doc + broken-link "
                "report. Returns {forward: {file: [targets]}, broken: "
                "[{file, line, target}], external_count}. One-shot "
                "validator + navigation primitive ('which docs link "
                "to BACKUP.md?'). Internal links are resolved relative "
                "to the source file's directory. Anchor-only ``#sec`` "
                "links are listed but never reported as broken."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
