---
description: Full pre-refactor audit of an MCP tool — server registration, query_engine impl, artefact dependencies, callers, tests
argument-hint: <mcp_tool_name>
allowed-tools: Read, Glob, Grep, Bash
---

Build a refactor-ready audit of MCP tool `$ARGUMENTS` (e.g. `find_symbol`, `ruff_violations`, `notify_log_search`) so its surface or implementation can be changed safely across the pipeline.

If `$ARGUMENTS` is empty: stop and ask which tool.

Layers to inspect (search in parallel):
1. **MCP registration** — `mcp_server.py`: locate the `@mcp.tool()` function literally named `$ARGUMENTS` (or wrapping it). Capture the docstring + parameter signature.
2. **Query layer** — `query_engine.py` (or sibling `*_inspector.py` / `*_index.py`): find the underlying function the MCP wrapper delegates to. Note its arguments, defaults, and return shape.
3. **Artefact dependencies** — Grep the query function for `_module_map.json`, `agent_*.json`, etc. List which artefacts it reads.
4. **Builder side** — `agent_map.py` and `parsers/`: find where those artefacts are produced. Capture the parser class + emit site.
5. **CLI exposure** — `cli.py` (and `bin/`): is the tool also exposed as a subcommand? If yes, link its dispatch line.
6. **Tests** — Glob `tests/test_*<tool>*.py` and Grep for the tool name across `tests/`. List hits.
7. **External callers in main repo** — Grep the parent repo (`../`) for `mcp__vc-context__$ARGUMENTS` to see who relies on this tool.
8. **Convention rules** — if `.vc-context/conventions.json` references this tool or its artefact, note that line.

Output sections:
- **MCP registration**: file:line + signature
- **Query function**: file:line + signature + return shape
- **Artefacts read**: list (with the file path each artefact lives at on disk)
- **Builder pipeline**: parser class file:line → emit site in agent_map.py:line
- **CLI subcommand**: file:line or `not exposed`
- **Tests**: existing test file:line list, plus a `Coverage gaps` bullet
- **External usages**: file:line in parent repo (and count)
- **Refactor checklist**: ordered list of what must change in lockstep (signature → query → mcp wrapper → cli → tests → docs)

Constraints:
- Each section ≤ 12 lines.
- Markdown links for every file:line reference.
- Do not dump bodies > 15 lines; link out and quote one-line summaries instead.
- After the report, end with one short paragraph: **"Safest first edit"** — which file/line to change first to minimise blast radius.
