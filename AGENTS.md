# vc-context-builder — Agent Instructions

This is the MCP server submodule. It lives at `.ai-context/` inside the parent project.

## Working here

- This is a **separate git repo** — commit here independently with `git commit`
- The parent project is at `../` (one level up)
- Run tests: `python3 -m unittest discover tests`
- Run lint: `uv run ruff check .`
- Snapshot check: `python3 tests/regen_snapshots.py --check`

## When editing MCP tools

Every MCP tool needs three things in sync — if you add or change one, check all three:

1. **Handler** in `mcp/dispatcher.py` — `_my_tool(self, args)` method + entry in `_handlers` dict
2. **Spec** in `mcp/specs/*.py` — JSON-Schema definition with description and inputSchema
3. **Query method** in `query_engine.py` or a mixin (`_query_symbols.py`, `_query_routes.py`, etc.)

After any change run `python3 tests/regen_snapshots.py --check` — the snapshot test catches
dispatcher/spec drift (a tool in dispatcher but not in specs, or vice versa).

## Key files

| File | Purpose |
|---|---|
| `query_engine.py` | Main query surface — delegates to mixins |
| `_query_symbols.py` | Symbol lookup, call sites, class shape |
| `call_sites.py` | Live TypeScript/Python call-site scanner |
| `mcp/dispatcher.py` | MCP tool name → query_engine method |
| `mcp/specs/` | JSON-Schema for every MCP tool |
| `mcp/metrics.py` | Per-call telemetry (agent_id, tokens, latency) |
| `mcp/rpc.py` | JSON-RPC framing + initialize handler |
| `agent_map.py` | Index builder — run after adding new parsers |

## MCP tool conventions

- Tools return focused JSON — no padding, no source text
- Empty results: always return `[]` or `null`, never `{"error": ...}` for "not found"
- Redirect hints: when a tool is deprecated or miscalled, return `{"note": "use X instead"}` rather than silent empty
- Baseline table: when adding a new tool add it to `_BASELINE_BYTES_PER_TOOL` in `mcp/metrics.py`
- Snapshot: run `python3 tests/regen_snapshots.py` after adding a tool to update the snapshot

## find_symbol vs semantic_search — when to use which

Measured cost difference (2026-05-25, real session data):

| Situation | Tool | Cost |
|---|---|---|
| Know the exact symbol name | `find_symbol("ClassName")` | **~28 tokens** |
| Know the concept, not the name | `semantic_search("service that handles X")` | **~195 tokens** |
| Guessing names with find_symbol | 3–4 attempts | ~400 tokens — **avoid** |

**Rule:** if you know the name → `find_symbol`. If you don't → `semantic_search` first, one call,
then `find_symbol` to get the body if needed.

Note: with the `local_hash` embedding provider scores are 0.28–0.40 (fuzzy name matching only).
With `sentence-transformers` or an API provider scores reach 0.7+ and semantic queries work reliably.
Check `~/.vc-context/<repo-hash>/embeddings/` to see which provider is active.

## Angular-specific tools (for lms-client parent project)

These tools were built for this Angular project — context when debugging them:

- `ng_ajs_find` — searches `app/` AND `src/app/downgraded/*.ajs.ts` bridge files
- `find_call_sites` — detects `inject(Service)` and `private x: Service` DI patterns (kind: "di"/"inject"/"call")
- `find_symbol` — case-insensitive fallback + `I`-prefix stripping for TS interfaces
- `ng_eslint_violations` — runs ESLint on the parent project; use this, not `lint_violations`
