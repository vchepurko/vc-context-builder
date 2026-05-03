# 🤖 vc-context-builder

Zero-dependency, auto-updating **code intelligence layer for LLM agents**.

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
| JS / TS | `react-component`, `react-hook`, `express-route`, `vue-composable` |

Detection mixes AST/regex pattern matching with file-path heuristics.

Filtered out (intentional): stdlib + third-party imports, private
helpers, empty `__init__.py` files, glue modules with nothing to expose.

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

## Installation

```bash
cd your-project-root
git submodule add https://github.com/vchepurko/vc-context-builder.git .ai-context
git -C .ai-context checkout main
./.ai-context/install.sh
```

`install.sh` is **pre-commit-aware**:

- If the parent project already uses [pre-commit](https://pre-commit.com),
  it appends `vc-context-builder` as a `local` hook and runs
  `pre-commit install`. **Existing hooks (ruff, pytest, …) keep working.**
- Otherwise it writes a standalone `.git/hooks/pre-commit`,
  preserving any prior one as `pre-commit.legacy.<timestamp>`.
- It does **not** add `.ai-context/` to `.gitignore` — that breaks
  submodule tracking.

After install, every `git commit` regenerates the artifacts in the
background and stages them automatically.

### Project mode vs local mode

Two ways to consume the artifacts. Pick whichever fits the team:

| Mode | Marker | Hook stages artifacts? | Whose call |
|---|---|---|---|
| **project** *(default)* | none | yes — next commit picks them up | whole team agrees |
| **local** | `.git/vc-context-local` | **no** — rebuilt only | one developer opts in |

Local mode lets a single developer enjoy the CLI / MCP integration
without polluting the team's git history with the JSON files. Toggle
per-clone (never propagates):

```bash
./.ai-context/install.sh --local-only   # this clone only
./.ai-context/install.sh --no-local     # back to project mode
```

`--local-only` also adds the artifact paths to `.git/info/exclude`
so untracked files don't clutter `git status`. If the artifacts
were already committed in the parent repo, `install.sh` prints a
hint with the `git rm --cached …` invocation needed to fully
untrack them — non-destructive, you opt in.

The marker lives under `.git/`, so it never crosses clones, branches,
or worktrees. You can also flip it manually:

```bash
touch  "$(git rev-parse --git-dir)/vc-context-local"   # enable
rm -f  "$(git rev-parse --git-dir)/vc-context-local"   # disable
```

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

Rule kinds: `forbid_import` (package name) / `forbid_call` (bare
function name). `match_path` is an `fnmatch` glob with `**` support.
`severity`: `error` flips `vc-context lint` exit code; `warn` / `info`
do not. Missing config = no rules = no error — opt-in by design.

More worked examples in [`USAGE.md`](USAGE.md).

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
