# Architecture

How vc-context-builder is wired together, in three layers.  Read
top-to-bottom — each layer assumes the one above is in place.

```
                ┌──────────────────────────────────────────────┐
                │            Agent / human consumer            │
                │   (Claude Code, Cursor, Codex CLI, shell)    │
                └────────────────────┬─────────────────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
        ▼                            ▼                            ▼
┌───────────────┐            ┌───────────────┐            ┌───────────────┐
│  MCP server   │            │  vc-context   │            │  agent_*.json │
│ (stdio JSON)  │            │     CLI       │            │  (fallback)   │
└───────┬───────┘            └───────┬───────┘            └───────┬───────┘
        │                            │                            │
        └──────────────┬─────────────┘                            │
                       │                                          │
                       ▼                                          │
            ┌─────────────────────┐                               │
            │    QueryEngine      │   Lazy reads + projections    │
            │  (query_engine.py)  ├──────────────────────────────►│
            └──────────┬──────────┘                               │
                       │                                          │
                       │   Reads on demand                        │
                       ▼                                          ▼
            ┌──────────────────────────────────────────────────────────┐
            │                    Indexer artefacts                     │
            │                                                          │
            │  agent_root.json     project-level: modules + roles      │
            │  agent_symbols.json  symbol → {file,line,kind,role,...}  │
            │  agent_tests.json    symbol → linked test                │
            │  agent_routes.json   FastAPI routes + JS callers         │
            │  agent_callbacks.json   aiogram callback_data → handler  │
            │  agent_fsm_flows.json   FSM state lifecycle              │
            │  agent_locale_keys.json   i18n key → values per lang     │
            │  agent_test_categories.json  unit / integration / unknown│
            │  <dir>/_module_map.json   per-folder file index          │
            └────────────────────────────▲─────────────────────────────┘
                                         │
                                         │ Built by
                                         │
                            ┌─────────────────────────┐
                            │      ContextBuilder     │
                            │     (agent_map.py)      │
                            │                         │
                            │  Walks project tree,    │
                            │  delegates per-file to  │
                            │  parsers, then builds   │
                            │  cross-file indexes.    │
                            └─────────────┬───────────┘
                                          │
                                          ▼
                            ┌─────────────────────────┐
                            │        Parsers          │
                            │   (parsers/*_parser.py) │
                            │                         │
                            │  PythonParser   AST     │
                            │  TsJsParser     regex+  │
                            │                 opt-AST │
                            │  JsonParser     targeted│
                            │  PhpParser      regex   │
                            │  DevOpsParser   regex   │
                            │  HtmlParser     regex   │
                            │  CssParser      regex   │
                            └─────────────────────────┘
```

## Layer 1 — Indexer (write side)

`agent_map.py:ContextBuilder` does a single pass over the project
tree.  For every recognised file it asks the matching parser for an
`{exports, dependencies}` payload and writes a per-folder
`_module_map.json`.  After all folders are mapped, cross-file builders
fold the data into the dedicated artefacts (symbol index, test
linking, route bridge, FSM flow, locale index, test categorisation).

The pass is idempotent and incremental:

* **File-level parse cache** (`parse_cache.py`) skips re-parsing files
  whose `(mtime, size)` haven't changed since the last build.  Cache
  invalidates wholesale when `.vc-context/conventions.json` or
  `roles.json` changes (the *epoch*).
* Build of a 100-file project: ~1s warm cache, ~3-5s cold.

Parsers are pluggable — drop a `parsers/<lang>_parser.py` subclassing
`BaseParser` and the auto-registry picks it up on the next run.

## Layer 2 — Query engine (read side)

`query_engine.py:QueryEngine` is the single entry point for every
read path.  Methods are lazy: each artefact loads on first access,
caches in-memory for the lifetime of the engine.  Public surface:

* Symbol queries: `find_symbol`, `find_symbols`, `get_callees`,
  `get_raised_exceptions`, `get_decorated_with`, `get_symbol_card`
* Project queries: `repo_map`, `summarise_module`, `get_file_card`,
  `list_modules`, `list_roles`, `find_by_role`
* Reverse lookups: `who_calls`, `find_call_sites`, `route_callers`,
  `find_callback`, `trace_fsm_flow`
* Git bridge: `get_changed_symbols`
* Bounded reads: `read_slice`, `find_symbol(include_body=true)`
* Lint/type/test: `lint_violations`, `mypy_violations`,
  `ruff_violations`, `ruff_format`, `run_check`, `find_test`
* Locales: `list_locale_keys`, `find_locale_key`, `get_locale_key`
* Telemetry: `get_session_metrics(quality=…)`

Token economy is enforced at the engine boundary:

* `find_symbol(fields=[...])` whitelist → ~30-token responses for
  "where is X?" lookups.
* `HIDE_BY_DEFAULT` set drops fact fields (`callees`, `raises`,
  `decorators`) from default responses; opt back in via `fields=` or
  the dedicated `get_*` tools.
* `read_slice` caps at 200 lines / 8KB.

## Layer 3 — Surfaces (consumer side)

Three surfaces, one engine:

* **MCP server** (`mcp_server.py` → `mcp/`) — stdio JSON-RPC.
  `Dispatcher` translates tool name → `QueryEngine` method.
  `mcp/specs/` declares the JSON-Schema for every tool, split by
  domain (`symbols.py` / `project.py` / `angular.py`).  `MetricsWriter`
  appends per-call telemetry to `~/.vc-context/metrics/`.
* **CLI** (`cli.py` + `cli_handlers.py` + `cli_renderers.py`) —
  argparse subcommands, full parity with the MCP surface.
* **JSON files** — direct read of `agent_*.json` for any text-LLM
  without MCP/CLI access.  Highest token cost, lowest setup cost.

Snapshot test (`tests/test_mcp_server.py`) pins the MCP tools/list to
`tests/fixtures/tools_list.json` so dispatcher↔spec drift fails CI.

## Telemetry & quality (observability)

Per-call JSONL sidecar (`mcp/metrics.py:MetricsWriter`) logs every
MCP call to `~/.vc-context/metrics/<repo-hash>-<date>.jsonl`.  No
tokens enter the agent's context unless `get_session_metrics` is
called explicitly.

`mcp/quality.py` runs three detectors over the same stream:

* `wasteful_pairs` — `find_symbol` → `read_slice` that should have
  been one `include_body=true` call
* `hot_rereads` — same `(tool, args)` repeated ≥3× — agent didn't
  cache
* `empty_streaks` — ≥3 consecutive empty results from one tool —
  wrong API or misspelled symbol

Each finding cites timestamps + tool calls so it's auditable.

## Conventions (project-specific behaviour)

`<project>/.vc-context/conventions.json` lets the parent project
declare project-specific rules, custom roles, and ignore patterns.
Config is read by:

* `conventions.py` — convention linter rules (`forbid_import`,
  `forbid_call`, `aiogram-no-bare-text-filter`, …)
* `custom_roles.py` — extend role tagging beyond the built-ins
* `agent_map.py` — `ignore_dirs` overrides (additive `+`, subtractive
  `-`, or replace)
* `checks.py` — whitelisted commands callable via `run_check`
* `http_callers.py` — Python-side route caller detection
* `mypy_inspector.py` / `ruff_inspector.py` — opt-in toggles

## Where to look first

| Task | Start with |
|---|---|
| Add an MCP tool | [`CONTRIBUTING.md`](CONTRIBUTING.md) → "Adding an MCP tool" |
| Add a language parser | [`CONTRIBUTING.md`](CONTRIBUTING.md) → "Adding a parser" |
| Understand how an agent uses this | [`playbooks/`](playbooks/) |
| See the surface | `vc-context --help` or [`MCP_SETUP.md`](MCP_SETUP.md) |
| Past releases | [`CHANGELOG.md`](CHANGELOG.md) |
| What's coming | [`ROADMAP.md`](ROADMAP.md) |
