# 🤖 vc-context-builder

Zero-dependency, auto-updating **code intelligence layer for LLM agents**.

## TL;DR

```bash
# 1. Drop into your project as a submodule (or clone standalone).
git submodule add https://github.com/<you>/vc-context-builder .ai-context

# 2. Build the index — scans the project, writes agent_*.json artefacts.
python3 .ai-context/agent_map.py

# 3. Wire the MCP server in your editor (one of):
#    Claude Code:  see MCP_SETUP.md
#    Cursor / Continue / Codex CLI / Aider: same file, copy-paste blocks.

# 4. Your agent now has ~40 tools that answer in 50–250 tokens.
#    Example claim → evidence flow:
find_symbol("MyClass", fields=["file","line"])  → 40 tokens
read_slice("path/to/file.py", 42, 58)           → 200 tokens
# vs. reading the whole file: 5,000+ tokens, no chance to cite the line.
```

Read the rest of this README for the full surface, or jump to a
playbook in [`playbooks/`](playbooks/) when you have a concrete task
type (bug hunt, impact analysis, refactor review).

---

The builder scans your project, parses ASTs + path heuristics, and emits
three artifacts that let an agent navigate the repo **without loading the
full source tree into its context window**:

| Artifact | Granularity | Typical use |
|---|---|---|
| `agent_root.json` | project-level | "what modules exist? which symbols are routes / migrations / scheduler-jobs?" |
| `agent_symbols.json` | one entry per symbol | "where is `add_admin` defined? what does it return?" |
| `<dir>/_module_map.json` | one entry per file | "what does `bot/handlers/admin.py` expose?" |

On top of those, two query surfaces:

| Surface | Who it's for | Token cost |
|---|---|---|
| **MCP server** | Claude Code, Cursor, Codex CLI ≥ 0.x, Continue, Aider+plugin | ~150 bytes per call — JSON files **never enter context** |
| **CLI** (`vc-context …`) | shell pipes, CI, generic LLM-with-shell-access agents, humans | ~200-2000 bytes per call |
| **JSON files** (fallback) | any text-LLM | reads the whole artifact (~hundreds of KB total) |

Same query engine behind all three. Pick the lightest your agent supports.

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
captured only on top-level declarations (matches the rest of the
indexer); method-level decorators (`@staticmethod`, `@property`)
don't appear.

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

See [`MCP_SETUP.md`](MCP_SETUP.md) — copy-paste blocks for Claude Code,
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
```

Exit codes: `0` on hit, `1` on miss / unknown role / unknown module /
error-severity lint hit.

### Conventions config (Feature A)

Drop a `.vc-context/conventions.json` in the **parent project root**
(NOT the submodule). Stdlib JSON, tiny schema:

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

More worked examples in [`USAGE.md`](USAGE.md).

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
`run_check` tool — handy when an agent needs to run tests / lint /
typecheck without arbitrary shell:

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

## How big is the win

Honest numbers from a real Python repo (~50K LOC, 1231 indexed symbols):

| Scenario | Without builder | With JSON tier | With MCP tier |
|---|---|---|---|
| "find one symbol" | ~10K tokens (grep + read candidates) | ~55K tokens (load `agent_symbols.json`) | **~150 bytes** |
| "list all webhooks" | ~30K tokens (grep + cross-check) | ~250 tokens (read `roles` block) | ~80 bytes |
| "describe one folder" | full file reads | ~2K tokens (one map) | ~2K tokens (returned as text) |

Discovery-phase savings sit around **30-50% of total session tokens** in
practice — not the marketing 70×. Edit + verify phases dominate; this
tool only attacks the orientation slice.

### Per-command savings (slash commands)

Every curated slash command in `templates/commands/*.md` ends with a
"Token cost" table that estimates the win versus the manual approach
(grep + Read fan-out). The agent prints a one-line summary at the
bottom of its response so the saving is visible per call:

```
_Used 5 MCP calls (~600 tokens) — saved ~9K vs reading sources directly._
```

Approximate ranges per command shape:

| Command shape | MCP cost | Manual cost | Savings |
|---|---|---|---|
| Symbol audit (`/audit-handler`, `/ng-audit-component`) | ~600–1500 tokens | 10–20K | 90–95% |
| Surface map (`/ng-overview`, `/ng-list-by-role`) | ~100 tokens | 50K+ | ~99% |
| Selector / call-site lookup (`/ng-find-selector`, `/refactor-callsites`) | ~200–700 tokens | 5–40K | 90–98% |
| Pattern-match (`/find-similar`) | ~400–700 tokens | 15–40K | 95–98% |

Numbers are estimates — the actual win depends on project size and
how deep the manual approach would go. The point is to make the
benefit *visible at the call site*, not to claim a fixed number.

---

## What this tool is **not**

- Not a runtime call-graph — `who_calls` is a best-effort static
  heuristic, not a true call graph.
- Not an LLM-summary engine — docstrings are extracted verbatim, not
  generated.
- Not a coverage / metrics tool — no test mapping, no hot-path data.
- Not a refactor sandbox — purely read-only outside of `git commit`.

---

## License & contributing

See `LICENSE`. Issues + PRs welcome — keep the zero-dependency rule.
