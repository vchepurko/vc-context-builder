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

### Real benchmark — MCP vs Bash on this project

Measured today on `lms-client` (Angular, ~1,400 TypeScript files).
Each task answered two ways: MCP tool call vs the equivalent Bash command an agent would run.

> **How to read:** tokens = output size ÷ 4 (standard LLM estimate).
> "Saved" = bash tokens − MCP tokens. Speed = bash_ms / mcp_ms.

| # | Task | Difficulty | MCP | Bash | Saved | Speed |
|---|---|---|---|---|---|---|
| 1 | Where is a service defined? | easy | **85 T** / 11 ms | 37 T / 1063 ms | −48 T ¹ | **100×** |
| 2 | Find interface by `I`-prefix | easy | **29 T** / 0 ms | 31 T / 797 ms | **+2 T** | **18 000×** |
| 3 | Find symbol by camelCase | easy | **35 T** / 0 ms | 31 T / 963 ms | −4 T | **9 700×** |
| 4 | Where is a service injected? (DI) | medium | **446 T** / 4 ms ² | 513 T / 35 ms | **+67 T** | **8.6×** |
| 5 | Angular component audit | medium | **96 T** / 1 ms | 600 T / 15 ms | **+504 T** | 12× |
| 6 | NgModule members | medium | **292 T** / 1 ms | 1307 T / 14 ms | **+1015 T** | 11× |
| 7 | Find AJS registration | medium | 1 T / 508 ms | 0 T / 2331 ms | — | 5× |
| 8 | Code health (lint + type + format) | hard | **105 T** / 7 ms | ~1 T / **30,005 ms** | — ³ | **4300×** |
| 9 | Who uses this service? (whole project) | hard | **283 T** / cold ⁴ | 799 T / 913 ms | **+516 T** | 0.1× ⁴ |
| 10 | Full DI inject graph for a module | hard | 2703 T / 24 ms ⁵ | 540 T / 46 ms | −2163 T ⁵ | 1.9× |

**Notes:**

¹ Tasks 1–3 use `include_body=False` — the question is "where is it defined?", not "show me the code".
Agents that need source can pass `include_body=True` or set `find_symbol_include_body: true` in conventions.json.
The convention setting inflates tasks 1–2 to 1912T / 1142T — 20–40× more than grep.
**Rule: use `include_body=False` for location queries, `include_body=True` only before an edit.**

² Task 4: now backed by a pre-built DI index (`agent_di_index.json`, 384 services).
Was 719T / 194ms with live scan — now **446T / 4ms** with index. MCP now beats grep on both speed AND tokens.
**Index-backed tools depend on freshness** — re-run `python3 .ai-context/agent_map.py` after structural changes.

³ Task 8: Bash ran `npm run lint` which takes **30 seconds** and returned only 1 token (timeout truncation).
MCP `check_health()` ran in 7 ms and returned lint + TypeScript + format results bundled.

⁴ Task 9: `who_calls` builds a reverse-dependency index on the **first call** (~3–10s cold).
Subsequent calls on the same session are instant (cached). Bash grep is faster on first call
but returns raw import lines the agent has to interpret.

⁵ Task 10: `ng_inject_graph` in **module mode** returns 60 structured records —
every injection point in the module tree with `{file, line, kind, service}`.
Bash returns 540T of raw constructor/inject lines the agent must parse manually.
MCP uses more tokens but delivers 21 unique services, typed — no parsing needed.

---

### Where MCP clearly wins

| Scenario | MCP | Bash | Why |
|---|---|---|---|
| Symbol location | 29–85 T / ~0 ms | 31–37 T / 800–1200 ms | Index O(1) vs grep walk |
| DI lookup (indexed) | **446 T / 4 ms** | 513 T / 35 ms | Pre-built DI index vs live scan |
| Component audit | 96 T | 600 T | Reads whole file vs structured facts |
| Module members | 292 T | 1307 T | Reads whole module vs extracted lists |
| Code health | 7 ms | 30,000 ms | Pre-wired check runner vs npm overhead |
| Project-wide usage | 283 T | 799 T | Structured + filtered vs raw grep flood |
| Full module DI graph | 60 typed records | raw lines | 21 unique services, no parsing needed |

### Where Bash wins (and why that's expected)

| Scenario | Why Bash is better |
|---|---|
| Symbol with body | `include_body=True` / convention inflates MCP 20× vs 1-line grep |
| First `who_calls` call | Cold index build (~3–10s) vs instant grep |
| `ng_inject_graph` token cost | 2703T structured vs 540T raw — more tokens but more useful |

### Index freshness — when to re-run `agent_map.py`

Some tools are index-backed and reflect a snapshot, not live files:

| Tool | Index file | When to re-index |
|---|---|---|
| `find_call_sites` (DI fast path) | `agent_di_index.json` | After adding/removing DI injections |
| `find_symbol` | `agent_symbols.json` | After creating/renaming classes |
| `ng_module_members` | `agent_symbols.json` | After changing NgModule declarations |
| `ng_list_routes` | `agent_ng_routes.json` | After changing routing config |

Live-scan tools (`who_calls`, `find_in_templates`) always reflect current files.

### The body convention tradeoff

`find_symbol_include_body: true` in `.vc-context/conventions.json` is a project-level setting
that embeds the source body in every `find_symbol` response. This is useful when the agent
immediately needs to read the code — saves a follow-up `read_slice` call. But it inflates the
first response 10–20×. Turn it off for "just find it" workflows:

```json
{ "find_symbol_include_body": false }
```

### Session-level data (802 calls, 7 days)

```
get_session_metrics(since="7d", group_by="tool", baseline=true)

Calls: 802  |  Total tokens: ~247k  |  Empty rate: varies by tool
Biggest saver:  ng_audit_component  84% savings vs reading files
Biggest waste:  find_symbol  43% empty (before today's fix — now ~5%)
                find_call_sites  94% empty (before DI fix — now ~10%)
                lint_violations  100% empty (now returns redirect hint)
```

### Agent quality visibility

Every call is recorded to `~/.vc-context/metrics/`:

```
get_session_metrics(since="today", group_by="agent_id")
→ compare claude-code vs cursor vs aider efficiency per tool
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
