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

## Submodule structure

This submodule lives at `.ai-context/` inside the parent project. Two MCP servers are wired:

| Server | Root | Purpose |
|---|---|---|
| `vc-context` | parent project | index + search over the main codebase |
| `vc-context-meta` | `.ai-context/` | self-index for contributors working on the submodule |

When working on the **parent project** — use `mcp__vc-context__*` tools.
When working on the **submodule itself** — use `mcp__vc-context-meta__*` tools.

## Efficiency rules (from session quality analysis)

These rules come from real session data — violations show up in `get_session_metrics` quality findings.

### read_slice — read wide, not twice

Two calls on the same file within 60 s = wasteful pair. Always fetch the full range you need in one call.

```
# BAD — two narrow calls
read_slice(file, 40, 70)
read_slice(file, 70, 110)

# GOOD — one wide call
read_slice(file, 40, 110)
```

Use `find_symbol(..., fields=["file","line","end_line"])` to get `start` and `end` upfront.

### find_in_file — regex alternation syntax

Use `|` (pipe), NOT `\|` (escaped pipe). In Python regex `\|` matches a literal `|` character.

```
# BAD — \| matches the character |, NOT alternation
find_in_file(file, pattern="loadUser\|saveUser")

# GOOD — alternation
find_in_file(file, pattern="loadUser|saveUser", use_regex=True)

# ALSO GOOD — simple substring, skip regex entirely
find_in_file(file, pattern="loadUser")
```

The tool auto-promotes patterns containing `|` or `\` to regex, but `\|` still means "literal pipe" in regex.

### find_call_sites — TS limitation

The DI fast-path (O(1)) only works for **Angular service/class names** indexed in `agent_di_index.json`.  
For plain TS function calls (not DI injection) it always returns empty.  
**Use `find_in_file` with the call expression as pattern instead.**

```
# WRONG for a plain TS function call
find_call_sites("formatDate")   # → []

# RIGHT
find_in_file("src/app/utils.ts", "formatDate(")
```

### ng_eslint_violations — only for lint audits

Spawns `npx eslint` over the full `src/` tree — expect 40+ s.  
Do **not** call this for navigation or "what does this code do?" questions.  
Pass a specific `path` (single file or directory) to reduce runtime.

```
# SLOW — scans entire src/
ng_eslint_violations()

# FASTER — scoped to one module
ng_eslint_violations(path="src/app/my-feature/")
```

Results are cached for 5 min within a session — calling it twice for the same path is free.

## Angular tools (Angular projects only)

For projects with Angular/AngularJS — full details in `.claude/commands/ng-*.md`:

- `ng-overview` — project shape snapshot
- `ng-impact` — blast radius of changing a symbol
- `ng-find-selector` — all templates using a selector
- `ng-audit-component` — pre-refactor component audit
- `ng-trace-service` — service injection graph
- `ng-route-impact` — route → guards → lazy children
