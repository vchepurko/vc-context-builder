# Repo as API — Personal Project Workshop

> Side-project by Vitalii Chepurko.  
> The idea: make AI agents navigate any codebase without reading it.

---

## The Problem I Ran Into

Every time I worked with Claude Code or Cursor on a large Angular project,
the agent would burn half the context window just *finding* things.

```
# Typical agent session without this layer:
Read("auth.service.ts")           →  4,800 tokens
grep -rn "AuthService" src/       →  3,500 tokens  
Read("auth.module.ts")            →  1,200 tokens
Read("jwt.interceptor.ts")        →  2,100 tokens
# Question: "where is AuthService injected?" — cost: ~11,600 tokens
```

The agent wasn't dumb. It just had no better tool than "read the whole file."

**The fix:** give it a query layer that returns structured facts, not source text.

```
find_call_sites("AuthService")  →  180 tokens
# [{file, line, caller, kind}] — exactly the answer, nothing else
```

---

## What the Project Does

**vc-context-builder** scans your project and builds a code intelligence layer.  
Drop it in as a git submodule, run one command, and your AI agent gets ~81 structured tools.

```bash
git submodule add https://github.com/vchepurko/vc-context-builder .ai-context
python3 .ai-context/agent_map.py   # builds the index
```

Three things happen:

### 1. JSON artifact index

The indexer parses your source tree (Python, TypeScript, Angular) and writes:

| File | Contains |
|---|---|
| `agent_root.json` | project shape, modules, routes, symbols registry |
| `agent_symbols.json` | one entry per exported symbol: file, line, callees, doc |
| `_module_map.json` | per-directory export map |

These JSON files are the ground truth. Every query reads from them — no grep, no file reads.

### 2. Generated markdown context files

This is the part most tools skip. The project generates **AGENTS.md** files —
structured markdown that agents load as instructions before starting a task.

```
project/
├── AGENTS.md                    ← project-wide: architecture, conventions, anti-patterns
├── src/app/modules/auth/
│   └── AGENTS.md                ← module-specific: auth invariants, known gotchas
└── .ai-context/
    ├── playbooks/
    │   ├── bug-hunt.md          ← step-by-step: how to investigate a bug in this repo
    │   ├── impact-analysis.md   ← how to assess blast radius before a refactor
    │   └── refactor-review.md   ← checklist before submitting a refactor PR
    └── docs/
        └── MCP_SETUP.md         ← copy-paste MCP config for Claude Code / Cursor / Aider
```

The agent reads the nearest `AGENTS.md` before every task.
It finds them via `find_local_agents_md(path)` — walks up the directory tree,
most-specific first. A bug in `src/app/modules/auth/` gets both the module rules
and the project-wide ones.

**Why this matters:** instead of the agent discovering conventions by reading source files,
it gets them as instructions. Fewer hallucinated patterns, fewer rounds of correction.

### 3. MCP server — 81 tools over stdio

The same index is exposed as an MCP server (Claude Code, Cursor, Continue, Aider).
Each tool returns a focused JSON response — no padding, no source text.

```
ng_audit_component("CollectionPlayerComponent")
→ {selector, inputs, outputs, injected_services, child_components, route, test_file}
# ~400 tokens — vs reading component + module + routes + DI tree: ~8,000 tokens
```

---

## Concrete Benefits

### Token savings

Real session data (56 MCP calls, Angular project, ~70 file edits):

| Approach | Tokens used | Tokens (baseline grep+Read) | Saved |
|---|---|---|---|
| With MCP layer | ~3,200 | ~5,800 | **44%** |

Per-tool cost vs alternative:

| Question | MCP tool | Cost | grep+Read cost |
|---|---|---|---|
| Where is X used? | `find_call_sites` | 180 T | 3,500 T |
| Angular component shape | `ng_audit_component` | 400 T | 8,000 T |
| Code health (lint+type+format) | `check_health` | 50 T | 33 separate calls |
| Who calls this function? | `who_calls` | 120 T | 2,800 T |

### Search speed

- Index lookup: **< 5 ms** (in-memory JSON, no disk reads per call)
- vs `grep -rn` on 1,000-file project: 800–2,000 ms
- vs reading a 500-line file: 200–400 ms round-trip in the MCP protocol

### Agent quality visibility

The project records every MCP call to `~/.vc-context/metrics/`:

```
get_session_metrics(since="today", group_by="tool", baseline=true)
→ {calls: 47, total_tokens: 3240, saved_tokens: 2890, savings_ratio: 0.47, ...}

get_session_metrics(since="7d", group_by="agent_id")
→ compare claude-code vs cursor vs aider — which agent uses the surface better
```

---

## How It Compares

| Tool | Approach | What's missing |
|---|---|---|
| **Cursor** | Embedding RAG | Closed, cloud, returns text not facts, no framework semantics |
| **Aider repo-map** | ctags graph | Read-only, no query surface, Aider-only |
| **repomix** | Full-repo text dump | Agent still reads everything |
| **Sourcegraph** | Enterprise code graph | Needs a server, doesn't live in the repo |
| **tree-sitter MCP** | Raw AST | Low-level nodes, not semantic answers |

**What's unique here:**
- Zero dependencies, zero infrastructure, zero cloud
- Lives inside the project as a submodule — versioned with the code
- Generates AGENTS.md context files, not just answers queries
- Framework-aware (Angular DI, aiogram FSM, AJS→Angular bridge)
- Agents can measure their own efficiency

---

## Where I Want to Take It

### Make agents more autonomous

Right now agents still decide *when* to call which tool. The next step is pre-loading
the right context automatically:

- **Auto-generated task AGENTS.md** — when an agent starts a task in a specific module,
  generate a focused markdown file: relevant symbols, test patterns in that module,
  known anti-patterns, open TODOs. Agent gets it as context before writing a single line.
- **Playbook auto-selection** — detect task type from the user prompt (bug / refactor /
  new feature) and pre-load the matching playbook automatically via `find_local_agents_md`

### Testing code intelligence (next major area)

Currently the project helps agents *read* code. Testing is the gap:

| Idea | What it does |
|---|---|
| `find_spec_for(symbol)` | Given a function, find the exact `it()` blocks that cover it |
| `find_handlers_without_tests` | List public methods/handlers with no spec coverage (basic version shipped) |
| **Test generation context tool** | Returns: signature + callers + what it calls + existing test patterns → enough for an agent to generate a real test without reading 10 files |
| **Test quality detector** | Spots `toBeTruthy()` stubs and `it('should work')` no-assertion tests; surfaces in `get_session_metrics` quality block |
| `coverage_for_role(role)` | Coverage ratio for a semantic domain ("auth", "payment") not a file path |
| **Mutation-style hints** | Without running mutation testing, flag tests where the only assertion is on return value — likely missing side-effect coverage |

### Agent instruction optimization

Use `group_by: agent_id` telemetry to auto-generate agent-specific recommendations:

```
# Example output (not yet built):
"claude-code makes 3× more empty ng_ajs_find calls than cursor.
Suggested addition to AGENTS.md for claude-code:
  AJS registrations live in src/app/downgraded/*.ajs.ts — search there first."
```

### Other improvements

- **Cross-language call graph** — TypeScript calling Python over HTTP, Python emitting events
  Angular subscribes to — graph stops at language boundaries today
- **Diff-aware re-index** — only re-parse files changed since last commit (now: full scan)
- **`explain_symbol` tool** — symbol card + callers + doc + test → a narrative ready for a PR description
- **Multi-repo / workspace support** — `find_call_sites` across package boundaries in monorepos

---

## Open Questions for Discussion

1. **Tool vs playbook for test generation?**  
   Tool = one call, structured response. Playbook = multi-step agent workflow.  
   Factual tools are easier to compose; playbooks are easier for agents to follow for complex tasks.

2. **Embedding hybrid?**  
   Pure AST misses runtime polymorphism and string-based DI tokens.  
   A small local embedding index (no cloud) would close those gaps.  
   Worth the first external dependency?

3. **How autonomous should AGENTS.md generation be?**  
   Currently hand-authored. Auto-generating from the index is possible but risks
   generating stale or wrong instructions if the index is out of date.

---

## Quick Numbers

| | |
|---|---|
| MCP tools | 81 |
| Typical call cost | 50–250 tokens |
| Token savings vs grep baseline | 40–55% |
| Search speed vs grep | ~200× faster |
| External dependencies | **0** |
| Index build time (200-file project) | < 10 s |
| Supported languages | Python, TypeScript, JavaScript, Angular |
| Supported MCP clients | Claude Code, Cursor, Continue, Aider, Codex CLI |
