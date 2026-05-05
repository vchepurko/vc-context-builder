---
description: Find structurally similar code inside vc-context-builder (parsers, builders, MCP tools, query helpers) for uniform refactoring
argument-hint: <symbol_or_pattern>
allowed-tools: Read, Bash, Glob, Grep
---

Find code in this submodule (vc-context-builder, the aiogram-aware indexer) similar to `$ARGUMENTS` so a refactor can be applied uniformly.

The repo is **Python only** and has these layers — recognise the layer of the argument and search siblings within it:

- `parsers/` — AST/string parsers that emit raw artefact rows
- `*_index.py`, `*_inspector.py`, `*_parser.py` — domain-specific indexers (callbacks, locales, FSM, HTTP, ruff, classes)
- `query_engine.py` — read-side helpers consumed by both CLI and MCP
- `mcp_server.py` — MCP tool registration (one function per public tool)
- `agent_map.py` — orchestrator that builds `agent_*.json` artefacts
- `bin/` — CLI entry points
- `tests/` (if any) — colocated under repo root or per-module

Workflow:
1. **Classify the argument**:
   - PascalCase ending in `Parser` / `Index` / `Inspector` → indexer class
   - snake_case starting with `mcp__` or matching `mcp_server.py` shape → MCP tool
   - bare snake_case → free function in query_engine or agent_map
   - file path / glob → use as scope directly
2. `Glob` for files in the matching layer, `Grep` for the symbol with `-n -C 2` to capture context.
3. For an indexer, also locate:
   - its **emit site** in `agent_map.py` (where the artefact is written)
   - its **consumer** in `query_engine.py` (where the artefact is read)
   - its **MCP wrapper** in `mcp_server.py` (the `@mcp.tool()` registration)
4. For an MCP tool, also locate:
   - the underlying `query_engine` function it calls
   - the artefact JSON it reads (look for `_module_map.json`, `agent_callbacks.json`, etc.)
5. **Group by similarity dimension**: same layer, same artefact, same parameter shape (e.g. `path_prefix=`, `limit=`).

Output:
```
## Primary: <symbol> at file:line

## Sibling implementations (same layer)
- file:line — <one-line role>

## Pipeline triplet (parser → builder → query → mcp)
- parser: file:line
- emit:   agent_map.py:line
- query:  query_engine.py:line
- mcp:    mcp_server.py:line

## Refactor candidates (ranked)
1. <group> — <why>
```

Constraints:
- Cap each group at 20; collapse the rest with `(+N more)`.
- Use file:line markdown links.
- Never paste >10 lines of code per site — link out instead.
- If the argument matches nothing, suggest 2 alternative searches (different layer, partial substring) and stop.
