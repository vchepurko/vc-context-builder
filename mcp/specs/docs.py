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
                "index (typo / outside tracked tree)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": (
                            "Repo-relative path to the ``.md`` file "
                            "(e.g. 'docs/OPS.md')."
                        ),
                    },
                },
                "required": ["file"],
            },
        },
        {
            "name": "find_doc_section",
            "description": (
                "Locate one section in ``file`` by heading text and "
                "return {level, text, line, end_line, anchor}. Pair "
                "with ``read_slice(file, start_line, end_line)`` for "
                "a surgical read of just that section — replaces the "
                "Bash 'grep -n ^## Foo && Read offset N limit M' "
                "two-step. Default fuzzy match is case-insensitive "
                "substring; set ``fuzzy: false`` for exact equality."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "header_pattern": {
                        "type": "string",
                        "description": (
                            "Substring (or exact text if ``fuzzy: "
                            "false``) of the heading to match."
                        ),
                    },
                    "fuzzy": {
                        "type": "boolean",
                        "description": (
                            "Default true — case-insensitive substring. "
                            "Set false for exact heading-text equality."
                        ),
                    },
                },
                "required": ["file", "header_pattern"],
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
                            "Optional repo-relative prefix filter "
                            "(e.g. 'docs/', 'changelog/')."
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
