# 🤖 vc-context-builder

**Retrieval-Augmented Generation (RAG) layer for code repositories.**
Turns your project into a structured knowledge base that LLM agents can
query in 50–250 tokens instead of reading multi-thousand-line source
files. Works as a git submodule, exposes an MCP server, a CLI, and
raw JSON — same engine behind all three.

## TL;DR

```bash
# 1. Add as a git submodule.
git submodule add https://github.com/vchepurko/vc-context-builder .ai-context

# 2. Build the index (first run ~30 s; incremental < 2 s on git-hook).
bash .ai-context/install.sh        # interactive: picks embedding + chat provider
# or non-interactive:
python3 .ai-context/agent_map.py

# 3. Wire the MCP server (Claude Code, Cursor, Continue, Aider…).
#    See docs/MCP_SETUP.md for copy-paste config blocks.

# 4. Your agent now has 80+ tools. Token cost comparison:
find_symbol("MyClass")                         →  ~150 tokens
read_slice("path/file.py", 42, 67)             →  ~200 tokens
summarise_module("business_logic/users")       →  ~300 tokens  (+ LLM summary)
# vs. reading source files manually: 5,000–50,000+ tokens per task.

# 5. Check what's running:
vc-context status
```

---

## What is this?

vc-context-builder is a **code RAG system** — it pre-indexes your
repository so an LLM agent never has to read full source files to answer
structural questions. Think of it as a compiled search index that turns
"what does this module do?" from a 5-file read into a single sub-second
query.

**Three-tier retrieval architecture:**

| Tier | How | Latency | Best for |
|---|---|---|---|
| **Structural** | AST + path heuristics → JSON artifacts | < 1 ms | Symbol lookups, role queries, call-site counts |
| **Semantic** | Vector embeddings (Ollama / OpenAI / local) → SQLite | 5–50 ms | Natural-language queries, "find code that does X" |
| **LLM-enhanced** | Ollama chat model on demand | 2–10 s | Module summaries, custom anti-pattern detection |

The system emits three artifacts that the agent queries without loading
source files:

| Artifact | Granularity | Typical use |
|---|---|---|
| `agent_root.json` | project-level | "what modules exist? which symbols are routes / scheduler-jobs?" |
| `agent_symbols.json` | one entry per symbol | "where is `add_admin` defined? what does it call?" |
| `<dir>/_module_map.json` | one entry per file | "what does `bot/handlers/admin.py` expose?" |

**Three query surfaces — same engine, pick the lightest your agent supports:**

| Surface | Who it's for | Overhead |
|---|---|---|
| **MCP server** | Claude Code, Cursor, Codex CLI, Continue, Aider | ~150 bytes/call — JSON never enters context |
| **CLI** (`vc-context …`) | shell, CI, generic LLM-with-shell agents, humans | ~200–2000 bytes/call |
| **JSON files** (fallback) | any text-LLM | reads whole artifact (~hundreds of KB) |

---

## Demo — what an agent actually sees

Real output from the submodule indexing **itself**.  The CLI views
below mirror byte-for-byte what an MCP host receives over the wire.

### Symbol overview — one call, ~250 tokens

```bash
$ vc-context card QueryEngine
QueryEngine
  file: query_engine.py:60-1869
  kind: class
  doc: Lazy-loading reader over the three artifact tiers.
  callees (102): _build_reverse_index, _by, _collect, _extract_body, ...
  test: tests/test_class_inspector.py:143  (test_engine_round_trip)
  callers: (none)
```

Replaces what would otherwise be `find_symbol` + `get_callees` +
`get_raised_exceptions` + `find_test` + `who_calls` — five separate
trips collapsed into one.

### Project shape at a glance

```bash
$ vc-context repo-map
=== 7 modules, 87 files, 348 exports ===
  .                26 files  98 exports
  ./mcp             6 files  15 exports
  ./parsers        10 files  18 exports
  ./tests          37 files 118 exports
  ...
```

Cheapest possible "what does this project look like?" — one MCP call
returning the same map you'd otherwise build by hand from
`agent_root.json`.

### Bounded source slice as evidence

```bash
$ vc-context slice query_engine.py 60 67
query_engine.py:60-67
     60  class QueryEngine:
     61      """Lazy-loading reader over the three artifact tiers.
     62
     63      Parameters
     64      ----------
     65      project_root : str
     66          Directory that holds ``agent_root.json``.  All other
     67          artifacts are looked up relative to it.
```

Pair with `find_symbol(..., fields=["file","line","end_line"])` for
the cite-the-exact-range pattern playbooks use.

### Telemetry — see how the agent is using the surface

```bash
$ vc-context stats --since 24h --quality
=== since 24h: 142 calls, ~3.2k tok, avg 6 ms, empty 9%, ok 100% ===
  find_symbol     87  (61%)  ~1812 tok   avg 4 ms   empty 4%
  who_calls       23  (16%)  ~480  tok   avg 7 ms   empty 22%   ← suspect
  read_slice      18  (13%)  ~720  tok   avg 2 ms   empty 0%
  ...

--- quality: 3 finding(s) ---
  [wasteful_pairs] (1)
    INFO  find_symbol('QueryEngine') → read_slice within 60s; could have used include_body=true
  [hot_rereads] (1)
    WARN  find_symbol({'name': 'Dispatcher'}) called 4× — consider caching
```

JSONL log under `~/.vc-context/metrics/` aggregates per project, per
day. `--quality` runs the wasteful-pair / hot-reread / empty-streak
detectors — actual *agent quality* signals derived from real usage.

---

## What gets indexed

- **Python** — top-level functions / classes via `ast`, with signatures + first-line docstring + decorator-based role detection.
- **PHP** — WordPress / WooCommerce hooks, traits, interfaces.
- **JS / TS / JSX / TSX** — top-level functions, arrow declarations,
  classes; signature capture for both arrow + function-statement form;
  JSDoc first-line summaries; built-in role detection for React
  components, React hooks, Express routes, Vue composables.
- **DevOps** — Dockerfiles, Makefiles, GitHub Actions.

Auto-detected built-in roles:

| Language | Roles |
|---|---|
| Python | `route`, `aiogram-handler`, `webhook`, `migration`, `scheduler-job`, `repository`, `service`, `api-client` |
| JS / TS | `react-component`, `react-hook`, `express-route`, `vue-composable`, `ng-component`, `ng-service`, `ng-module`, `ng-pipe`, `ng-directive`, `ng-guard` |

Detection mixes AST/regex pattern matching with file-path heuristics.

Filtered out (intentional): stdlib + third-party imports, private
helpers, empty `__init__.py` files, glue modules with nothing to expose.

### Angular routes

Beside the existing HTTP route bridge (`agent_routes.json`, Express /
FastAPI), Angular projects get a parallel artifact `agent_ng_routes
.json` extracted from `RouterModule.forRoot([…])` /
`RouterModule.forChild([…])` / `provideRouter([…])` / bare
`const X: Routes = [...]` declarations.

Each record carries `{path, component, file, line, lazy, redirect_to,
guards}`. MCP tools sit on top:

* `ng_list_routes` — dump every route.
* `ng_route_for_path` — `'users/:id'` → record(s); exact match first,
  substring fallback so `'users'` finds `users/:id` too.
* `ng_routes_for_component` — reverse lookup, "where is HomeComponent
  mounted?".

Slash command `/ng-route-impact <path-or-Component>` ties them
together with the component / guard symbol lookups for a one-screen
answer.

The artifact is omitted on non-Angular projects — no empty file in
the tree.

### Incremental builds

Re-running `agent_map.py` after editing one file used to re-parse
*every* file in every dirty directory. As of Feature S, a file-level
parse cache at `.vc-context/_parse_cache.json` (gitignored) skips
re-parsing files whose `(mtime, size)` matches the previous build.

Concrete numbers from this repo (~50K LOC, 317 indexed files):

| Run | Time | Cache |
|---|---|---|
| Cold (no cache) | ~5 s | 317 misses |
| Warm (no edits) | ~2.5 s | 100 % hits |
| Warm (one file changed) | ~2.5 s | 99.7 % hits |

Invalidation is automatic: any change to `.vc-context/conventions
.json` or `.vc-context/roles.json` bumps the cache *epoch* and forces
a full rebuild — so custom-roles edits never produce stale role
tags.  When the parser source itself changes (a submodule bump),
bump `parse_cache.CACHE_VERSION` to force the same.

The build prints a one-liner summarising the hit ratio:

```
INFO: Parse cache: 303/303 hits (100.0%), 0 misses.
```

### Test linking

`agent_tests.json` pairs each indexed symbol with its nearest test
file + function so `find_test(X)` is one tool call away. Two parallel
walkers feed the index:

* **Python** — `tests/**/test_*.py` AST-walked. Reference resolution
  follows imports + `patch("a.b.X")` strings. Co-location fallback by
  `test_<basename>*.py` glob.
* **TypeScript / JavaScript** — `**/*.spec.{ts,tsx,js,jsx,mjs,cjs}`
  scanned via regex (no JS AST in stdlib). Imports + `describe(...)`
  / `it(...)` / `test(...)` blocks become the test surface. Co-location
  by `<basename>.spec.ts` next to `<basename>.ts` (Angular convention).
  `node_modules`, `dist`, `coverage`, `.angular`, `.next` etc. are
  skipped.

Symbols without a test get `null` so the API stays uniform.

### Custom roles

Built-ins not enough? Drop a `.vc-context/roles.json` in the parent
project root to declare extra roles via glob path + regex matchers.
The submodule then works on Go / Ruby / WordPress / anything without
code changes.

```jsonc
{
  "roles": [
    {
      "id": "wordpress-hook",
      "match_path": "**/*.php",
      "match_call": "(add_action|add_filter)",
      "priority": 5
    },
    {
      "id": "rails-controller",
      "match_path": "app/controllers/**/*.rb",
      "match_function_name": "_controller$",
      "priority": 10
    }
  ]
}
```

Matchers (any combinable per role): `match_path` (glob with `**` and
`{a,b}` brace alternation), `match_decorator_or_call` (regex against
decorators or registration calls), `match_function_name` (regex on
the symbol name), `match_function_returns` (regex on the function
body), `match_call` (regex on the function body for a called symbol),
`match_kind` (one of `func` / `async-func` / `class`).

Built-in roles default to priority `0`; custom roles default to `5`,
so they override built-ins by default. Bump `priority` higher to win
over other custom rules.

Missing config = built-in roles only, no error. Opt-in by design.

---

## Quickstart

```bash
cd your-project-root
git submodule add https://github.com/vchepurko/vc-context-builder.git .ai-context
./.ai-context/install.sh

# Optional but recommended — generates .vc-context/conventions.json
# tuned for your stack (Django / FastAPI / Flask / Angular / bot / generic).
# Idempotent: safe to re-run; only adds keys that are missing.
python3 .ai-context/cli.py init
```

**That's it.** The installer:

1. Builds the agent_*.json artifacts (one-shot).
2. Installs a native `git pre-push` hook that rebuilds them before every push.
3. Hides those artifacts from your `git status` (local mode by default — no team-wide change).
4. Writes a project-rooted **`.mcp.json`** so Claude Code auto-wires
   `vc-context` MCP without per-developer config.
5. Drops curated slash commands (`/find-similar`, `/audit-handler`,
   `/refactor-callsites`) into `.claude/commands/`.

Reload your editor — done. No `pip install`, no global config, no
`~/.claude/mcp.json` edits. Submodule pull updates everything in place.

Want MCP startup to refresh stale indexes automatically? Add the
initial-setup flag:

```bash
./.ai-context/install.sh --auto-reindex=60
```

That writes `.vc-context/conventions.json → auto_reindex`, so any MCP
client that starts `vc-context` rebuilds the index when it is older than
60 minutes. Use a different number for a shorter/longer interval.

### Want artifacts committed (team-wide)?

Project mode stages and commits the artifacts so every clone shares them:

```bash
./.ai-context/install.sh --shared
```

Toggle back any time with the default flagless invocation. The mode
marker lives under `.git/`, so it never crosses clones, branches, or
worktrees.

### Other flags (rarely needed)

| Flag | Purpose |
|---|---|
| `--no-mcp` | Skip writing `.mcp.json` (you manage MCP elsewhere). |
| `--no-commands` | Skip copying slash commands. |
| `--force-commands` | Overwrite existing slash command files. |
| `--auto-reindex[=N]` | Enable MCP-startup reindex when artifacts are older than N minutes (default 60). |
| `--auto-reindex-minutes N` | Same as above, friendlier for scripts. |
| `--pre-commit` | Use the pre-commit framework instead of native pre-push (legacy; may race with autofix hooks). |
| `--shared` | Stage artifacts on push (team mode). |

`--help` prints the full reference.

### Pulling new commands after a submodule bump

`install.sh` is a one-shot. When a `git submodule update --remote
.ai-context` brings in new slash commands (e.g. the `ng-*` set), you
need to pull them into `.claude/commands/`:

```bash
bash .ai-context/sync-commands.sh           # add new ones, leave locals alone
bash .ai-context/sync-commands.sh --force   # also overwrite local edits
```

Default behaviour matches `install.sh` — only missing files are
copied; existing commands are skipped to protect customisations. Use
`--force` to reset everything to upstream.

For code / MCP tool updates, the submodule itself is enough: the bin
wrappers and `.mcp.json` point at `.ai-context`, so new tools are
available after the editor/MCP server restarts. Re-running
`./.ai-context/install.sh` is safe and idempotent: it rebuilds indexes,
leaves existing MCP entries alone, copies only missing slash commands,
and preserves local command edits unless `--force-commands` is passed.
`python3 .ai-context/cli.py init` is also idempotent; it only adds
missing config keys unless `--force` is used.

### Indexing the submodule against itself (contributor mode)

When you're working *on* vc-context-builder (e.g. PRs into the
submodule itself, not the parent project), the parent's MCP server
can't help you navigate the builder's own code — it points at the
parent root, not at `.ai-context/`. Bootstrap a self-index:

```bash
bash .ai-context/self-index.sh           # one-shot
bash .ai-context/self-index.sh --watch   # also install a pre-commit
                                          # hook that rebuilds the
                                          # index on every commit
```

Then wire a second MCP entry alongside the existing one in your
parent project's `.mcp.json`:

```jsonc
{
  "mcpServers": {
    "vc-context": {
      "command": "python3",
      "args": [".ai-context/mcp_server.py", "--root", "."],
      "type": "stdio"
    },
    "vc-context-self": {
      "command": "python3",
      "args": [".ai-context/mcp_server.py", "--root", ".ai-context"],
      "type": "stdio"
    }
  }
}
```

Tools then surface under two prefixes — `mcp__vc-context__*` for the
parent project and `mcp__vc-context-self__*` for the submodule itself.
Skip this entirely if you only consume vc-context as a library; the
self-index is purely a contributor convenience.

The submodule ships its own `.vc-context/conventions.json` with a
`checks` whitelist, so `mcp__vc-context-self__run_check("test")` /
`run_check("lint")` / `run_check("format-check")` /
`run_check("snapshots-check")` work out of the box — no need to
shell out to bash for the submodule's own quality gates.

### Optional: TypeScript AST upgrade for Angular metadata

The default TS/JS parser uses regex to extract Angular decorator
metadata (selector / templateUrl / providedIn / standalone / inputs /
outputs).  Regex is fast and zero-config but misses dynamic shapes
like `providedIn: SOME_TOKEN` or computed selectors.

Opt into the AST path when accuracy matters:

```jsonc
// .vc-context/conventions.json
{
  "typescript_ast": {"enabled": true}
}
```

Requirements: the *target project* needs Node (any recent version)
and `typescript` installed (locally via `npm i typescript --save-dev`
or globally).  vc-context itself stays stdlib-only — when Node /
typescript aren't reachable the parser falls back to regex silently.

Performance: each Angular `.ts` file triggers one Node spawn
(~50 ms warm).  For a 500-component project that's ~25 s of extra
indexing on a full rebuild.  Incremental builds amortise it; if
that's still too slow, leave `enabled: false` and live with the
regex result.

---

## Quick taste — three ways to ask the same question

> "Where is `add_admin` defined and what's its signature?"

**MCP** (Claude Code / Cursor / Codex CLI):

```jsonc
// agent calls a tool — no JSON enters its context window
tools/call: find_symbol("add_admin")
→ {"file": "bot/api_client/staff.py", "kind": "async-func",
   "params": "(user_id: int, role: str='manager')",
   "doc": "POST /api/admin/staff/admins — add or update an admin row.",
   "role": "api-client"}
```

### Token economy — paying only for what you ask

Every symbol record carries 1-indexed `line` (start) and, for Python,
`end_line` — so callers can `Read(file, offset=line, limit=…)` without
a follow-up grep. The default record (~150 tokens of MCP envelope +
payload) covers the "tell me everything" case. For tighter loops:

```jsonc
// "Jump to X" — beats `bash grep` on cost (~40 tokens)
find_symbol("add_admin", { "fields": ["file", "line"] })
→ {"file": "bot/api_client/staff.py", "line": 42}
// → Read("bot/api_client/staff.py", offset=42, limit=20)

// Skip the follow-up Read — embed the source body inline
find_symbol("add_admin", { "include_body": true })
→ {... "body": "async def add_admin(user_id: int, role: str='manager'):\n    ..."}

// Three lookups, one round-trip (~150 tokens vs ~3×135)
find_symbols(["add_admin", "remove_admin", "list_admins"],
             { "fields": ["file", "line", "kind"] })
→ {"add_admin": {...}, "remove_admin": {...}, "list_admins": {...}}
```

Body extraction uses Python AST (`get_source_segment`) for `.py` and a
regex-anchored line slice for JS/TS, capped at
`BODY_SNIPPET_LINES`/`BODY_SNIPPET_MAX_BYTES`. JS/TS records carry
`line` only (no `end_line`) — the regex parser doesn't track block
end positions; the slice cap is the practical upper bound.

**CLI** (shell, CI, generic agent):

```bash
$ vc-context find add_admin
add_admin  bot/api_client/staff.py
  async-func (user_id: int, role: str='manager')  [api-client]
  POST /api/admin/staff/admins — add or update an admin row.
```

**JSON fallback** (universal):

```bash
$ jq '.add_admin' agent_symbols.json
{ "file": "bot/api_client/staff.py", "kind": "async-func", ... }
```

Same answer. Different cost per look-up.

---

## Telemetry — see how the agent is using the MCP surface

Every MCP call emits one JSONL line to
`~/.vc-context/metrics/<repo-hash>-<YYYY-MM-DD>.jsonl` (override via
`VC_CONTEXT_METRICS_DIR`). Aggregate via:

```bash
$ vc-context stats --since 24h --by tool
=== since 24h: 142 calls, ~3.2k tok, avg 6.1 ms, empty 9%, ok 100% ===
  find_symbol     87  (61%)  ~1812 tok   avg 4.0 ms   empty 4%
  who_calls       23  (16%)  ~480  tok   avg 7.5 ms   empty 22%   ← подозра
  read_slice      18  (13%)  ~720  tok   avg 2.1 ms   empty 0%
  get_callees      9   (6%)  ~110  tok   avg 1.0 ms   empty 11%
  list_roles       5   (3%)  ~80   tok   avg 3.0 ms   empty 0%
```

Or via MCP for an LLM-readable summary:
`get_session_metrics(since="24h", group_by="tool")` →
`{calls, total_tokens, avg_t_ms, empty_ratio, ok_ratio, by_tool}`.

`empty_ratio` flags wasted round-trips (calls returning `null` /
`[]` / `{}` / `{total: 0}`); high values mean either the symbol
doesn't exist or the agent's calling the wrong tool. `approx_tokens`
uses the `bytes // 4` heuristic — rough but stable for trends.

Pass `--no-metrics` to `mcp_server.py` to opt out (writer becomes a
no-op; no disk activity).

### Quality findings — wasteful pairs, hot rereads, empty streaks

Add `--quality` to the CLI (or `quality: true` to the MCP call) to
get a Phase-2 audit on top of the raw counters:

```bash
$ vc-context stats --since 24h --quality
=== since 24h: 142 calls, ~3.2k tok, avg 6.1 ms, empty 9%, ok 100% ===
  find_symbol     87  (61%)  ~1812 tok   avg 4.0 ms   empty 4%
  ...

--- quality: 3 finding(s) ---
  [wasteful_pairs] (1)
    INFO  find_symbol('QueryEngine') → read_slice within 60s; could have used include_body=true
  [hot_rereads] (1)
    WARN  find_symbol({'name': 'Dispatcher'}) called 4× — consider caching
  [empty_streaks] (1)
    WARN  find_call_sites returned empty 3 times in a row — wrong query or misspelled symbol?
```

Detectors (see `mcp/quality.py` for thresholds):

- **wasteful_pairs** — `find_symbol(name=X)` followed by
  `read_slice(file=…)` within 60s when `include_body=true` was
  *not* passed. One round-trip would have sufficed.
- **hot_rereads** — same `(tool, args_summary)` queried ≥3× — cache
  the result instead.
- **empty_streaks** — ≥3 consecutive empty results from the same
  tool — wrong API or misspelled symbol.

Each finding cites evidence (timestamps + tool calls) so an agent
or human can audit the claim.

---

## Evidence-based answers — fact tools

Three Tier-1 tools support claims with AST-derived evidence so the
agent can stop guessing and start citing:

```jsonc
// "What does this function call?" — sorted, deduped, no source read.
get_callees("do_work")
→ ["fetch", "log_event", "validate"]

// "What does this raise?" — class names from `raise X(...)`.
get_raised_exceptions("do_work")
→ ["ValueError", "HTTPError"]

// Cite the proof — a bounded slice (≤200 lines / 8KB), no shell.
read_slice("pkg/work.py", start=42, end=58)
→ {"file": "pkg/work.py", "start": 42, "end": 58,
   "content": "def do_work():\n    fetch()\n    ...",
   "truncated": false}
```

`callees` and `raises` are excluded from the default `find_symbol`
response (they can be long); pass `fields=["callees","raises"]` to
opt back in, or use the dedicated tools above. Pair `read_slice`
with `find_symbol(..., fields=["file","line","end_line"])` for the
"jump to evidence" pattern: one round-trip to find, one to read the
exact range that proves the claim.

---

## Card-shaped tools — one-call answers

When a playbook needs a full picture before deciding what to read,
use the *card* tools — each replaces a 3-5 call sequence with a
single ~250-token response:

```bash
$ vc-context card QueryEngine
QueryEngine
  file: query_engine.py:28-1759
  kind: class
  doc: Lazy-loading reader over the three artifact tiers.
  callees (102): _build_reverse_index, _by, _collect, _extract_body, ...
  test: tests/test_class_inspector.py:143  (test_engine_round_trip)
  callers: (none)
```

```bash
$ vc-context file-card backend/routes/admin.py
backend/routes/admin.py
  dependencies: fastapi, ...
  roles: route: 7
  exports (7):
    - list_admins:42  async-func [route]  -- GET /api/admin/staff/admins ...
    - add_admin:58  async-func [route]  -- POST /api/admin/staff/admins ...
    ...
```

```bash
$ vc-context repo-map
=== 14 modules, 117 files, 412 exports ===
  ./bot/handlers      12 files   89 exports  [aiogram-handler]
  ./database/repositories  8 files  41 exports  [repository]
  ./services           7 files   26 exports  [service]
  ...
```

```bash
$ vc-context changed --base main
12 changed symbol(s):
  add_admin       bot/api_client/staff.py:42-58   (async-func) [api-client]
  list_admins     backend/routes/admin.py:42-50   (async-func) [route]
  ...
```

```bash
$ vc-context decorated dataclass
3 symbol(s) decorated with 'dataclass':
  Config  config.py:7  (class)
  ...
```

MCP equivalents: `get_symbol_card`, `get_file_card`, `repo_map`,
`get_changed_symbols`, `get_decorated_with`. `decorators` are
captured at the top level AND folded in from method-level
decorators on the enclosing class — so `get_decorated_with(
"staticmethod")` / `("property")` / `("abstractmethod")` find the
class even though the indexer only carries top-level symbols.

---

## Playbooks — task-shaped MCP recipes

When you have a concrete task type, open the matching playbook for a
pre-baked MCP sequence + output format:

- [Bug investigation](playbooks/bug_investigation.md) — "Why does X
  fail?", stack trace in hand.
- [Impact analysis](playbooks/impact_analysis.md) — "What breaks if I
  change X?", before refactor.
- [Refactoring review](playbooks/refactoring_review.md) — reviewing
  your own or someone else's refactor / PR.

See [playbooks/README.md](playbooks/README.md) for when to add a new
one.

---

## Wiring an MCP host

See [`docs/MCP_SETUP.md`](docs/MCP_SETUP.md) — copy-paste blocks for Claude Code,
Cursor, Codex CLI, Continue, and a generic stdio host.

Smoke test (no agent needed):

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python3 .ai-context/mcp_server.py | head -1
```

Expect `serverInfo.name = "vc-context"`.

---

## CLI cheatsheet

```bash
# Symlink once, then use from anywhere in the project tree:
ln -s "$PWD/.ai-context/bin/vc-context" /usr/local/bin/vc-context

vc-context find <symbol>            # one symbol — file / kind / params / doc / role / test
vc-context semantic-search "query"  # Phase 5 semantic symbol search
vc-context recall-experience "ctx"  # Phase 5 local decision/pattern recall
vc-context remember-experience --context "ctx" --content "rule"
vc-context calls <symbol>           # who imports the file containing <symbol>
vc-context role <role>              # every symbol with that role tag
vc-context module <relative/path>   # one folder summary
vc-context roles                    # role → count map
vc-context modules                  # scanned folder list
vc-context build                    # rebuild artifacts manually

# Action-tier (Features A / B / C):
vc-context lint                     # convention violations from .vc-context/conventions.json
vc-context test <symbol>            # nearest existing test for <symbol>
vc-context coverage                 # symbol-test linking ratio per role
vc-context route <path>             # backend route record
vc-context route-callers <path>     # JS/TS call-sites that hit a route

vc-context find <symbol> --json     # machine-readable; works on every subcommand
vc-context --root /abs/path …       # query a different project

# Project setup:
vc-context init                     # generate .vc-context/conventions.json (idempotent)
vc-context init --force             # re-detect stack, reset disabled_tool_groups only
```

Exit codes: `0` on hit, `1` on miss / unknown role / unknown module /
error-severity lint hit.

### Conventions config (Feature A)

Drop a `.vc-context/conventions.json` in the **parent project root**
(NOT the submodule). Generate a starter with `vc-context init` (detects
your stack automatically) or write it by hand. Stdlib JSON, tiny schema:

#### Filtering MCP tools per project

Use `disabled_tool_groups` to hide irrelevant tool groups from the MCP
`tools/list` response — the client never sees them, so they don't
pollute the agent's tool menu:

```json
{
  "disabled_tool_groups": ["angular", "locale", "fsm", "notify_log", "route"]
}
```

Available groups:

| Group | Tools hidden |
|---|---|
| `angular` | `ng_*` (14 tools) + `find_in_templates` |
| `locale` | `list_locale_keys`, `find_locale_key`, `get_locale_key` |
| `fsm` | `trace_fsm_flow` |
| `notify_log` | `notify_log_search`, `notify_log_stats` |
| `route` | `route_callers`, `route_for_js_call` |
| `docs` | `get_doc_toc`, `find_doc_section`, `list_docs`, `find_doc_xref`, `docs_link_graph` |

For individual tools not covered by a group, use `disabled_tools`:

```json
{
  "disabled_tools": ["devops_card", "repo_map"]
}
```

Both keys can coexist. Takes effect on the next MCP server restart
(i.e. new conversation / editor reload).

#### MCP-startup auto-reindex

If you want every agent to get a fresh-enough index without remembering
to call `rebuild_index`, enable:

```json
{
  "auto_reindex": {
    "enabled": true,
    "interval_seconds": 3600
  }
}
```

On MCP startup, `vc-context` checks `agent_root.json`; if the index is
missing or older than the interval, it runs `agent_map.py --root <project>`.
The installer writes this block for you with
`./.ai-context/install.sh --auto-reindex=60`.

`vc-context init` picks sensible defaults for common stacks:
- **django / fastapi / flask** → disables `angular`, `locale`, `fsm`, `notify_log`, `route`
- **angular** → disables `fsm`, `notify_log`, `locale` (keeps `ng_*`)
- **bot** (aiogram / telebot / pyrogram) → disables `angular`, `route` (keeps `fsm` for state machines)
- **generic** → disables `fsm`, `notify_log`

```json
{
  "rules": [
    {
      "id": "handlers-via-api-client",
      "description": "Bot handlers must not import database.* directly.",
      "match_path": "bot/handlers/**/*.py",
      "forbid_import": "database",
      "severity": "error"
    },
    {
      "id": "no-print",
      "description": "No print() — use the logger.",
      "match_path": "**/*.py",
      "forbid_call": "print",
      "severity": "warn"
    }
  ]
}
```

Rule kinds:

- `forbid_import: "<pkg>"` — fail if a Python file imports `<pkg>`
  (either `import <pkg>...` or `from <pkg>... import ...`).
- `forbid_call: "<name>"` — fail if a Python file calls `<name>(...)`
  at the leaf level (bare names only, not `obj.<name>(...)`).
- `forbid_decorator_regex: "<regex>"` — fail when a function/class
  declaration carries any decorator whose textual form (the literal
  `@<expr>`) matches the given regex. Catches framework-specific
  antipatterns without baking them into the parser. Example for
  aiogram: `^@router\.message\(F\.text\)$` flags every
  `@router.message(F.text)` (match-any-text swallower) while leaving
  `@router.message(F.text == "X")` and state-bound forms alone.

`match_path` is an `fnmatch` glob with `**` support.
`severity`: `error` flips `vc-context lint` exit code; `warn` / `info`
do not. Missing config = no rules = no error — opt-in by design.

More worked examples in [`docs/USAGE.md`](docs/USAGE.md).

### HTTP-clients config (Feature E)

A second optional block in the same `conventions.json` teaches the
route bridge about your project's internal HTTP wrapper, so call sites
that funnel through `get_client().post("/api/foo", …)` are linked
back to the FastAPI route alongside JS/TS callers:

```json
{
  "http_clients": [
    {
      "factory": "bot.api_client.get_client",
      "methods": ["post", "get", "patch", "delete"],
      "first_arg_is_path": true
    }
  ]
}
```

Each entry adds Python call-sites to the matching route's
`callers_python: [{file, line, raw, function}, …]` list inside
`agent_routes.json`. Empty config = JS-only (legacy behaviour).

### Whitelisted check runner (Feature J)

A third optional block exposes safe-to-run commands to the MCP
`run_checks` tool — handy when an agent needs to run tests / lint /
typecheck without arbitrary shell. `run_checks` accepts a list of names
and runs them **in parallel** (up to 4 concurrent workers), so an agent
can fire lint + tests in one call instead of two sequential round-trips:

```json
{
  "checks": {
    "test":             ["uv", "run", "pytest", "-q"],
    "test-unit":        ["uv", "run", "pytest", "-q", "-m", "not integration"],
    "test-integration": ["uv", "run", "pytest", "-q", "-m", "integration"],
    "lint":             ["uv", "run", "ruff", "check"],
    "typecheck":        ["uv", "run", "mypy", "."]
  }
}
```

Each value is an **argv list** (no shell, no string-splitting). The
runner executes with `subprocess.run(args, cwd=project_root,
timeout=300)` and returns `{returncode, duration_ms, stdout_tail,
stderr_tail, summary}` — last 50 lines of each stream, plus a pytest-
style summary line when one is recognisable. Unknown name → `-2`,
timeout → `-1`, spawn failure → `-3`. No `checks` block → `list_checks`
returns `[]`.

For targeted checks, use the object form and declare which extra argv
tokens are safe. Fixed argv-list checks refuse `args`; object checks
append `run_check(args=[...])` only after every token passes
`args_policy`:

```json
{
  "checks": {
    "pytest": {
      "cmd": ["uv", "run", "pytest"],
      "args_policy": {
        "allow_paths": true,
        "path_roots": ["tests"],
        "allow_flags": ["-q", "-x"],
        "allow_flag_values": ["-k", "--maxfail"],
        "deny_flags": ["--pdb"]
      }
    }
  }
}
```

Example MCP calls:

```jsonc
// Run one check
run_checks(names=["lint"])

// Run two checks in parallel — returns both results in one response
run_checks(names=["lint", "test-unit"])

// Targeted check with extra args
run_checks(names=["pytest"], args=["-q", "tests/test_locales.py", "-k", "placeholders"])
```

Refused extra args return `-4`. Cache keys include `(name, args,
git_state_hash)`, so targeted runs do not collide with each other.

---

## LLM-enhanced tools

When a `chat_provider` is configured in `.vc-context/conventions.json`,
two additional capabilities activate. Both degrade gracefully — they
return the normal result without a `summary`/hits when Ollama is not
running or the model is not pulled.

### `summarise_module` — natural-language module descriptions

```jsonc
summarise_module("business_logic/users")
→ {
    "directory": "business_logic/users",
    "files": { ... },
    "summary": "Handles user account lifecycle: creation, password
                recovery, group assignments, and profile validation.
                Depends on business_logic/core for DB access and
                signals for cross-domain side-effects."
  }
```

The summary is generated once per session (cached by prompt hash) so
repeated calls are instant. Agents can use it to orient to an unfamiliar
module in one call instead of reading 10+ files.

Configure via `conventions.json`:

```json
{
  "chat_provider": {
    "name": "ollama",
    "model": "qwen2.5-coder:1.5b",
    "host": "http://localhost:11434"
  }
}
```

### `find_anti_patterns` — LLM-based custom rules

Beyond the built-in AST detectors, you can define project-specific
anti-patterns in plain English. The LLM evaluates each function/method
chunk independently and returns hits in the same `{rule, file, line,
function, evidence}` format as static detectors. Results are cached by
file mtime so unchanged files are not re-scanned within a session.

```json
{
  "anti_patterns": [
    {
      "name": "raw-sql-in-view",
      "description": "Direct SQL queries inside view functions instead of the service layer",
      "scope": "web_services/**/*.py"
    },
    {
      "name": "business-logic-in-serializer",
      "description": "Database writes or complex business logic inside DRF serializer.save()",
      "scope": "**/*serializers.py"
    }
  ]
}
```

`list_anti_patterns()` returns both static and custom rule names.
`install.sh` now includes an interactive step to configure the chat
provider and pull the model (~1 GB, one-time).

---

## Extending the parsers

Drop a new parser into `parsers/` that subclasses `BaseParser`:

```python
# parsers/go_parser.py
from parsers.base_parser import BaseParser

class GoParser(BaseParser):
    extensions = ['.go']

    def extract(self, file_path: str):
        # … parse a .go file, return:
        return {
            "exports": [{"name": "...", "kind": "func", "params": "(...)"}],
            "dependencies": ["..."],
        }
```

Auto-registered on import via `BaseParser.__init_subclass__`. Add the
import to `parsers/__init__.py` and you're done — no central registry
to update.

---

## MCP ↔ CLI parity

Every MCP tool has a CLI equivalent (or vice-versa). The same query
engine runs both — use MCP inside an editor session, CLI for scripts
and CI.

| Category | MCP tool | CLI command |
|---|---|---|
| **Navigation** | `find_symbol` | `vc-context find <name>` |
| | `find_symbols` | `vc-context find <n1> <n2> …` |
| | `semantic_search` | `vc-context semantic-search "query"` |
| | `search_doc_text` | `vc-context search-doc "query"` |
| | `who_calls` | `vc-context calls <name>` |
| | `find_by_role` | `vc-context role <role>` |
| | `list_roles` | `vc-context roles` |
| | `list_modules` | `vc-context modules` |
| **Cards** | `get_symbol_card` | `vc-context card <name>` |
| | `get_file_card` | `vc-context file-card <path>` |
| | `repo_map` | `vc-context repo-map` |
| | `summarise_module` | `vc-context module <path>` |
| | `get_changed_symbols` | `vc-context changed [--base <ref>]` |
| **Evidence** | `read_slice` | `vc-context slice <file> <start> <end>` |
| | `get_callees` | `vc-context callees <name>` |
| | `get_raised_exceptions` | `vc-context raises <name>` |
| | `get_decorated_with` | `vc-context decorated <decorator>` |
| | `inspect_class` | `vc-context inspect <class>` |
| **Quality** | `run_checks` | `vc-context check <name> [name…]` |
| | `lint_violations` | `vc-context lint` |
| | `ruff_violations` | `vc-context ruff` |
| | `mypy_violations` | `vc-context mypy` |
| | `find_anti_patterns` | `vc-context anti-pattern <rule>` |
| | `list_anti_patterns` | `vc-context anti-patterns` |
| | `find_handlers_without_tests` | `vc-context untested` |
| | `coverage_for_role` | `vc-context coverage` |
| **Tests** | `find_test` | `vc-context test <name>` |
| | `classify_tests` | `vc-context classify-tests <path>` |
| | `tests_by_category` | `vc-context tests-by-category` |
| **Impact** | `impact` | `vc-context impact <name>` |
| | `find_call_sites` | `vc-context call-sites <name>` |
| **Docs** | `list_docs` | `vc-context docs` |
| | `find_doc_section` | `vc-context doc-find <query>` |
| | `get_doc_toc` | `vc-context doc-toc <file>` |
| **Status** | `status` | `vc-context status` |
| | `get_session_metrics` | `vc-context metrics` |
| | `rebuild_index` | `vc-context build` |

Tools in the `angular`, `locale`, `fsm`, `notify_log`, `route`,
`devops` groups can be disabled per-project via `disabled_tool_groups`
in `conventions.json` — they won't appear in `tools/list` at all,
keeping the agent's tool menu focused.

---

## How big is the win

Honest numbers from a real Python repo (~50K LOC, 5600+ indexed symbols):

| Scenario | Without builder | With MCP structural tier | With MCP semantic tier | With LLM tier |
|---|---|---|---|---|
| "find one symbol" | ~10K tokens (grep + read) | **~150 tokens** | ~200 tokens | — |
| "list all routes" | ~30K tokens | ~80 tokens | — | — |
| "describe one module" | full file reads (~5K+) | ~2K tokens (map) | — | ~300 tokens + summary |
| "find custom anti-pattern" | manual code review | — | — | scans files automatically |
| "what does X call?" | open file + search | ~100 tokens | — | — |
| "find code doing X" | grep + judge hits | — | ~200 tokens | — |

Discovery-phase savings sit around **30–50% of total session tokens** in
practice. Edit + verify phases dominate; this tool attacks the
orientation and evidence-gathering slices.

**Potential metrics for your project:**

- **Tool calls per task** — track `get_session_metrics` at session end;
  aim for < 15 tool calls on a typical bug-fix task.
- **Tokens per session** — compare sessions with vs. without MCP enabled
  in your editor's usage dashboard.
- **Round-trips to evidence** — `find_symbol` + `read_slice` = 2 calls
  for any fact. Without builder: grep → read candidates → open file = 5+.
- **Stale-hit rate** — `rebuild_index` should run on every git push hook;
  monitor with `status` (stale = True means the agent is working from an
  old index).
- **LLM call savings** — `summarise_module` cached per session: if a
  module is referenced 5× in one session, only 1 LLM call is made.

### Per-command savings (slash commands)

Every curated slash command in `templates/commands/*.md` ends with a
"Token cost" table. Approximate ranges:

| Command shape | MCP cost | Manual cost | Savings |
|---|---|---|---|
| Symbol audit (`/audit-handler`) | ~600–1500 tokens | 10–20K | 90–95% |
| Surface map (`/ng-overview`) | ~100 tokens | 50K+ | ~99% |
| Call-site lookup | ~200–700 tokens | 5–40K | 90–98% |
| Pattern-match | ~400–700 tokens | 15–40K | 95–98% |
| Module orientation | ~300 tokens (+ LLM) | 10K+ | 97%+ |

---

## What this tool is **not**

- Not a runtime call-graph — `who_calls` is a best-effort static
  heuristic, not a true dynamic call graph.
- Not a coverage / metrics tool — no hot-path data, no branch coverage.
- Not a refactor sandbox — purely read-only outside of `git commit`.
- Not a substitute for reading code — it reduces *how much* you read,
  not whether you read at all. Use `read_slice` after locating symbols.

---

## License & contributing

See `LICENSE`. Issues + PRs welcome — keep the zero-dependency rule.
