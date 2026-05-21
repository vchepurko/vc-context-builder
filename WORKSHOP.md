# Workshop: Repo as API — Code Intelligence Layer for AI Agents

A practical overview of what this project is, why it exists, how it compares to alternatives,
and where it is going. Written for developers who use AI-assisted coding but haven't seen
this layer before.

---

## The Problem

When you ask Claude Code (or Cursor, Aider, Codex CLI) to fix a bug or add a feature, the agent
needs to understand the codebase. The default strategy is: read files. Lots of them.

```
# What happens without a code intelligence layer:
Read("src/app/modules/auth/auth.service.ts")      →  4,800 tokens
Read("src/app/modules/auth/auth.module.ts")        →  1,200 tokens
Bash("grep -rn 'AuthService' src/")               →  3,500 tokens
Read("src/app/core/interceptors/jwt.interceptor.ts") → 2,100 tokens
# Total for one "where is AuthService injected?" question: ~11,600 tokens
```

Most of those tokens carry no signal for the actual question. They pay for imports, comments,
unrelated methods, and whitespace the agent has to mentally filter.

**This project replaces that pattern with surgical queries that return structured facts:**

```
find_call_sites("AuthService")  →  180 tokens
# Returns: [{file, line, caller, kind}] — exactly what was needed, nothing else
```

Across a real 56-call session on this codebase: **44% fewer tokens** vs the grep+Read baseline.

---

## What It Is

**vc-context-builder** is a code intelligence layer that you drop into any project as a git submodule.

```
git submodule add https://github.com/<you>/vc-context-builder .ai-context
python3 .ai-context/agent_map.py   # build index once (< 10 s on a 200-file project)
```

It scans your project, builds structured JSON artifacts, then exposes them through three
query surfaces — all backed by the same engine:

| Surface | Who uses it | Token cost per call |
|---|---|---|
| **MCP server** (~81 tools) | Claude Code, Cursor, Continue, Aider | 50–250 tokens |
| **CLI** (`vc-context …`) | Shell scripts, CI, any LLM-with-shell | 200–2,000 tokens |
| **JSON artifacts** (fallback) | Any text-LLM, no tooling required | Full file, read once |

Zero external dependencies. Pure stdlib Python. Works offline. No embeddings, no vector DB,
no cloud.

---

## How It Compares

| Project | Approach | Key difference |
|---|---|---|
| **Cursor / Copilot** | Embedding RAG + vector search | Closed, cloud, returns text fragments — not structured facts |
| **Aider repo-map** | ctags-based dependency graph | Aider-only, no query surface, no framework semantics |
| **repomix** | Packs the whole repo into one file | Text dump — the agent still has to read it all |
| **Sourcegraph / Cody** | Full code graph + cloud infra | Requires a server; doesn't live inside the project |
| **tree-sitter MCP tools** | Raw AST over MCP | Low-level (AST nodes, not "who calls this function?") |
| **OpenCtx** | Generic MCP context providers | No framework-specific knowledge |

**What makes this different:**

1. **Zero infrastructure** — submodule pattern, lives in the repo, version-controlled alongside code
2. **Structured facts, not text fragments** — `find_call_sites` returns `[{file, line, caller}]`,
   not a grep dump the agent must parse
3. **Framework-specific semantics** — Angular DI inject graph, aiogram FSM flow traces,
   AJS→Angular bridge detection — not generic RAG
4. **Token efficiency as a first-class metric** — every call is recorded; agents can run
   `get_session_metrics` and see their own efficiency, by tool, by agent
5. **Self-indexing** — the submodule can analyze itself; contributors use the same tools
6. **Agent quality feedback loop** — `wasteful_pairs`, `hot_rereads`, `empty_streaks` detectors
   surface inefficient patterns in real sessions

---

## Demo Scenarios

### 1. "Where is this service used?" — 180 tokens vs 11,000

```
# MCP tool call:
find_call_sites("AuthService", include_tests=false)

# Response (180 tokens):
[
  {"file": "src/app/core/guards/auth.guard.ts", "line": 12, "caller": "AuthGuard", "kind": "inject"},
  {"file": "src/app/modules/login/login.component.ts", "line": 8, "caller": "LoginComponent", "kind": "inject"}
]
```

### 2. Angular component deep-dive — one call, full picture

```
ng_audit_component("CollectionPlayerComponent")

# Returns: selector, templateUrl, inputs, outputs, injected services,
#          child components, route it's mounted at, test file location
# ~400 tokens vs reading component + module + routes manually (~8,000 tokens)
```

### 3. Code health roll-up — 1 call instead of 33

```
check_health()

# Runs lint + mypy + ruff in one round-trip
# Returns: {lint: [], mypy: [], format: "ok"} when clean — ~50 tokens
# Real sessions were making 11+11+11 = 33 separate calls for this
```

### 4. Agent self-monitoring

```
get_session_metrics(since="today", group_by="tool", baseline=true)

# Returns:
{
  "calls": 47,
  "total_tokens": 3240,
  "empty_ratio": 0.12,
  "baseline": {
    "saved_tokens": 2890,
    "savings_ratio": 0.47
  },
  "by_tool": {
    "check_health": {"calls": 3, "tokens": 120, "empty_ratio": 0.0},
    "find_call_sites": {"calls": 8, "tokens": 640, "empty_ratio": 0.0},
    ...
  }
}
```

### 5. Agent tracking (new — agent_id per session)

```
get_session_metrics(since="7d", group_by="agent_id")

# Lets you compare how different agents (claude-code, cursor, aider)
# use the tool surface — which tools they prefer, empty rates per agent,
# token efficiency per agent. Useful for improving agent instructions.
```

---

## Current Architecture

```
.ai-context/
├── agent_map.py          # Index builder — AST + heuristic parsers
├── query_engine.py        # Core query surface (1,400 LOC)
├── _query_symbols.py      # Symbol-fact methods extracted for clarity
├── _query_inspectors.py   # Lint / type / format / test runners
├── _query_routes.py       # Route bridge (AJS → Angular)
├── mcp/
│   ├── server.py          # stdio JSON-RPC server
│   ├── dispatcher.py      # Tool name → query_engine method
│   ├── metrics.py         # Per-call JSONL telemetry + aggregation
│   ├── rpc.py             # JSON-RPC framing + initialize handler
│   └── specs/             # JSON-Schema definitions (81 tools)
├── parsers/               # Language-specific AST extractors
│   ├── ts_js_parser.py    # TypeScript / JavaScript (+ optional TS AST worker)
│   ├── python_parser.py   # Python (ast module)
│   └── ...
├── tests/                 # 65+ test files
└── playbooks/             # Task-shaped guides for agents
```

**Artifacts written at index time:**
- `agent_root.json` — project shape, module list, route/migration/scheduler registries
- `agent_symbols.json` — one entry per exported symbol (file, line, kind, callees, doc)
- `_module_map.json` — per-file export map (one per directory)

---

## What's Planned

### Near-term (1–3 PRs)

**Testing code intelligence** — this is the next major area:
- `find_handlers_without_tests` — surfaces public methods/handlers that have no test file covering them (shipped basic version; deeper coverage analysis planned)
- `coverage_for_role` — given a semantic role ("auth", "payment"), return coverage ratio for that domain
- **Test generation context tool** — given a symbol, return: its signature, callers, what it calls, existing test patterns in the project, edge cases visible from the AST. Enough for an agent to generate a meaningful test without reading 10 files.
- **Test quality detector** — spots tests that only assert `toBeTruthy()` or are `it('should work')` stubs; surfaces them in `get_session_metrics` quality block
- **`find_spec_for(symbol)`** — given a function name, find its spec file and the specific `it()` blocks that cover it (reverse of current `find_test` which goes file→test)
- **Mutation-style coverage hints** — without running mutation testing, detect tests where the only assertion is on the return value (no side-effect assertions) — flag as weak coverage

### Medium-term

- **Cross-language call graph** — TypeScript calls Python (via HTTP); Python emits events that Angular subscribes to. Right now the graph stops at language boundaries.
- **`explain_symbol` tool** — combines `get_symbol_card` + surrounding callers + doc + test into a narrative the agent can use directly in a PR description
- **Diff-aware re-index** — only re-parse files changed since last commit (currently full re-index each time)
- **Bash usage tracking integration** — `record_bash_usage` is shipped; next step is surfacing it in quality detectors so "true MCP win" includes Bash avoided
- **Per-agent instruction optimization** — use `group_by: agent_id` telemetry to generate agent-specific AGENTS.md recommendations ("claude-code makes 3× more empty `ng_ajs_find` calls than cursor — add this instruction")

### Open questions / discussion

- **Should test generation be a tool or a playbook?** Tool = one structured response; playbook = multi-step agent workflow. Both have value; the question is whether we want to keep the MCP surface purely factual.
- **Embedding hybrid** — pure AST has blind spots (runtime polymorphism, string-based DI tokens). A small embedding index for "semantically similar functions" would close those gaps without requiring cloud. Worth the dependency cost?
- **Multi-repo support** — monorepo / workspace awareness: `find_call_sites` across package boundaries.

---

## Installation (5 minutes)

```bash
# In your project root:
git submodule add https://github.com/<you>/vc-context-builder .ai-context
python3 .ai-context/agent_map.py

# Claude Code — add to .mcp.json:
{
  "mcpServers": {
    "vc-context": {
      "command": "python3",
      "args": [".ai-context/mcp_server.py"],
      "env": { "VC_CONTEXT_PROJECT_ROOT": "." }
    }
  }
}

# Verify:
vc-context health
```

See `docs/MCP_SETUP.md` for Cursor, Continue, and Aider configurations.

---

## Numbers from Real Sessions

| Metric | Value |
|---|---|
| MCP tools available | 81 |
| Typical call cost | 50–250 tokens |
| Token savings vs grep+Read baseline | 40–55% |
| Index build time (200-file project) | < 10 s |
| Index build time (1,000-file project) | < 45 s |
| External dependencies | **0** |
| Languages indexed | Python, TypeScript, JavaScript, Angular templates |
| Supported MCP clients | Claude Code, Cursor, Continue, Aider, Codex CLI |
