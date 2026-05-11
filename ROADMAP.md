# 🗺️ Roadmap

What's shipped, what's planned, what's deferred.  Updated alongside
the code — if you spot an inconsistency, file an issue.

---

## ✅ Shipped

### Indexing core
- AST-based Python parser (top-level symbols, callees, raises,
  decorators including method-level fold-in, line / end_line)
- Heuristic JS / TS parser + optional TypeScript-AST upgrade for
  Angular metadata
- Targeted JSON parser (`package.json`, `tsconfig.json`, `composer.json`)
- DevOps parser (Dockerfile, Compose, Makefile, GitHub Actions)
- File-level parse cache (per-`(mtime, size)` skip on rebuild)
- Self-index mode — index the submodule against itself for contributors

### Query surface
- **MCP server** (~41 tools)
  - Symbol cards (`get_symbol_card`), file cards (`get_file_card`),
    repo map (`repo_map`)
  - Reverse / forward call lookup (`who_calls`, `get_callees`,
    `find_call_sites`)
  - Decorator search (`get_decorated_with`)
  - Typed fact-check (`verify` — `exists` / `calls` / `decorated` /
    `raises`)
  - Git-aware: `get_changed_symbols`
  - Bounded source reads (`read_slice` + `find_symbol(include_body=true)`)
  - Lint / type / format / test runners (`run_check`,
    `lint_violations`, `mypy_violations`, `ruff_violations`)
  - Angular: components / routes / inject-graph / overview
  - Locales: list / find / get
- **CLI** (`vc-context`) — full parity with the MCP surface
- **JSON fallback** — read artifacts directly when no MCP / CLI

### Telemetry & quality
- Per-call JSONL telemetry sidecar (`~/.vc-context/metrics/`)
- Quality findings: `wasteful_pairs`, `hot_rereads`, `empty_streaks`
- `vc-context stats --quality` for human review

### Documentation
- Task-shaped playbooks: bug investigation, impact analysis,
  refactoring review
- Cross-tool entry point (`AGENTS.md` in parent project)

### CI / dev experience
- GitHub Actions: tests (3.11 + 3.13 matrix) + lint (ruff + mypy +
  snapshots)
- Pre-commit hooks: ruff + mypy + tests + snapshots + sanity
- Snapshot tests for the MCP tools/list (catches dispatcher/spec drift)

---

## 🔜 Planned (next 1–3 PRs)

### Code quality
- **Continue `query_engine.py` split** — symbols / cards / repo_map
  / git / checks group still lives in the facade (~1500 LOC after
  the first three mixins landed). Extract `_QuerySymbolsMixin` next
  for the biggest remaining cluster.
- Replace inline `import re` / `import ast` with module-level imports
  where the perf benefit is negligible.

### Anti-pattern detectors
A small set of stat-only detectors layered on top of the existing
indexer. Zero LLM, zero new runtime deps — pure set-difference over
already-extracted artefacts. Each ships with a `--strict` mode that
turns it into a lint-blocker via `conventions.json`.

- `find_orphan_callbacks` — `callback_data` referenced from
  keyboards / templates with no matching `@router.callback_query`
  body. Set-difference between two existing artefacts; ~50 LOC.
- `find_handlers_without_tests` — `aiogram-handler` ∩ ¬`coverage_for_role`.
  Already half-built (`coverage_for_role` exists); needs a thin
  aggregator + MCP wiring.
- `find_locale_drift` — keys present in one language file but absent
  in sibling language(s). The locale loader already enumerates them.
- `find_anti_patterns(rule_name)` — runs registered AST detectors;
  ships with `aiogram-state-check-in-body` (silent-dispatch killer)
  as the first rule. Designed so new rules slot in as plain functions
  in a registry.

### Per-folder agent rules convention
- Project-side companion: per-folder `AGENTS.md` (vendor-neutral) for
  invariants too local for the root file. Submodule responsibility
  here is a `find_local_agents_md(path)` MCP helper so any agent
  can discover folder-scoped rules without filesystem walks.

### Gap-closers from real session usage

Concrete tools called out by 24h-telemetry + observed Bash fall-backs
during a real klodchikknifes session (May 2026). Each is a 1-2-day add,
captured here so we don't keep re-deriving the need:

- **`find_pattern_in_configs(pattern, kinds=['env','yaml','caddy',…])`** —
  fast indexed grep over `.env*`, `docker-compose*.y*ml`, `Caddyfile`,
  `*.conf`, `*.ini`. Today every "where is `GOOGLE_OAUTH_*` referenced"
  falls back to Bash `grep -rn` because the indexer ignores non-code
  surfaces. ASCII-only scan, cached per-`(mtime, size)` like the
  Python parser.
- **`list_migrations()`** — alembic-aware: returns current head in DB,
  list of files in `alembic/versions/`, drift between model columns and
  applied migrations. Replaces `ls alembic/versions/` + `alembic current`
  + manual model inspection.
- **`find_orm_field_usage(model, column)`** — for "every read/write of
  `Product.photo_file_id`": today `grep -rn photo_file_id` returns 50+
  lines of noise. With ORM-aware parsing we can return only `.<column>`
  attribute accesses and `Model(column=...)` constructors.
- **`devops_card()`** — single roll-up of Dockerfile, docker-compose
  services, Caddyfile rules, scheduler entries (APScheduler jobs +
  cron files we discover), `update-all.sh`-style scripts. Avoids the
  current scattered "where does deploy actually live?" investigation.
- **`summarise_module(folder, *, filter, max_tokens)`** — current call
  on `tests/` overflows with 16k tokens / 3243-line dump and tells the
  caller to chunk-read. Better contract: bail early with `truncated:
  true` + filter parameter (e.g. `filter='test_photo*'`) so a typed
  question gets a typed answer.
- **`empty_batch` telemetry quality finding** — `find_symbols(['a','b','c'])`
  returning all-null today doesn't fire any quality warning because
  `empty_streak` looks at consecutive call-level emptiness, not
  batch-internal. Detector should treat "all keys null in one batch"
  as 1 empty streak signal so agents notice they're asking for ghosts.

Each of these has a real use case, but commits to non-trivial design.
Waiting for ~1–2 weeks of real `vc-context stats --quality` data
before deciding which to pull from this list.

- **Phase 3 eval harness** — `task → agent answer → verifier → score`.
  Needs (a) curated tasks the user actually runs, (b) baseline metrics
  from the telemetry sidecar.  Building too early = optimising for the
  wrong workload.
- **Profiles (`default` / `strict` / `fast`)** — per-session config
  that toggles evidence-citation strictness.  Skip until telemetry
  shows agents drifting between modes.
- **`get_db_writes(model)`** — SQLAlchemy / Django ORM detector.
  Project-specific patterns; high false-negative risk.  Design once we
  have a target stack with enough variety to validate.
- **JSON parser expansion** — `Cargo.toml` (TOML, not JSON, but same
  shape), `pyproject.toml`, `Pipfile`. Add when we hit a project that
  needs them.
- **Generic code-analysis skill docs** — explicitly *not* doing this.
  CLAUDE.md + AGENTS.md + 3 playbooks already cover the workflow side;
  more skill files would just rot.
- **Conversation-mining harness** — nightly job that reads agent
  transcripts (Claude Code first, others as drivers land), extracts
  patterns the agent kept correcting, and proposes new
  `conventions.json` rules / `AGENTS.md` lines via PR. **Strictly
  human-in-the-loop** — no autonomous repo writes. Distinct from the
  "no LLM summaries" out-of-scope rule below: this mines *agent
  behaviour*, not source-code semantics. Needs ~1-2 weeks of
  baseline telemetry before scope is locked.
- **Telemetry-driven prompts** — when `get_session_metrics(quality=true)`
  shows recurring `wasteful_pairs` / `hot_rereads` / `empty_streaks`,
  surface a one-line suggestion in `vc-context stats` output (e.g.
  "you read foo.py 5×; try `find_symbol(include_body=true)`"). Pure
  static rule layer over the JSONL sidecar; no new dependency.

---

## 🌅 Phase 4 — Local-first + anti-redundancy (vision)

**Status**: vision-stage. No code yet. Captured here so the direction
is explicit and the next concrete PRs can be sliced from it.

### The pain we're solving

Today's MCP surface is **reactive**: the agent must know what to ask
before it can find an existing helper. Result observed in the wild —
an agent on an unfamiliar repo built a feature from scratch instead
of reusing existing utilities, because nothing surfaced "we already
have this". That's not a rules problem; it's a **proactive-knowledge
problem**.

### Three-layer artefact model

A clean separation of what's committed vs. what lives per-machine.

| Layer | Where | Lifecycle | Examples |
|---|---|---|---|
| **Shared** (project-wide) | `<repo>/` committed | Hand-edited; reviewed in PRs | `AGENTS.md`, `CLAUDE.md`, `.vc-context/conventions.json` |
| **Local-per-repo** | `~/.vc-context/<repo-hash>/` | Auto-built, gitignored | indexes, embeddings, decision log, personal notes |
| **Global** (cross-project) | `~/.vc-context/global/` | Optional dotfiles sync | workflow preferences, skill patterns |

Concretely the local-per-repo folder gets:
```
~/.vc-context/<repo-hash>/
├── index/           agent_*.json, _module_map.json — auto-rebuild
├── embeddings/      semantic vectors per symbol
├── learned/         decisions.jsonl — patterns figured out about this repo
└── personal.md      private notes you don't want in the repo
```

**Side benefit** of moving the index to local-only: kills the git-diff
noise from `_module_map.json` rebuilds on every commit (today every
edit dirties 5–20 module maps). The repo only carries hand-curated
artefacts; everything mechanical regenerates on demand in <2s.

### Anti-redundancy mechanism

A new MCP surface that fires *before* the agent writes new code:

- `find_similar_to(text, top_k=5)` — semantic search across symbols.
  Phase A: fuzzy match on `name + role + first-line doc` (no
  embedding model needed). Phase B: real embeddings via local
  `nomic-embed-text` (Ollama) or one-shot Claude API at index time.
- `record_decision(action, target, reason)` — append to
  `learned/decisions.jsonl`. Captures "agent reused X instead of
  writing new Y" or "agent decided to write new Z because A, B, C".
- `replay_decisions(query)` — when a similar context recurs, surface
  the past decision so the agent doesn't relitigate it.

The agent's pre-write protocol becomes:
1. Before adding a new symbol, call `find_similar_to(description)`.
2. If similarity ≥ threshold → propose reuse to the user.
3. Whatever happens (reuse / new / variant), call `record_decision`.
4. Next session, `replay_decisions` primes context with prior calls.

### Promotion path local → shared

Decisions that prove repeatable can graduate from local to committed
project rules:
```
vc-context decisions list                # show local log
vc-context decisions promote <id>        # open PR adding rule to AGENTS.md
                                         #  or .vc-context/conventions.json
```
Keeps the shared layer curated (only validated patterns land there)
while letting the local layer be noisy and exploratory.

### Cross-machine sharing (deferred sub-question)

For "I want my own agent-knowledge to follow me across machines":
- Path A — symlink `~/.vc-context/global/` into a private dotfiles
  repo. Plain-files; no infra.
- Path B — opt-in cloud bucket sync. Bigger scope, encryption
  questions, defer until path A is insufficient.

For "I want to share patterns across teammates": that's the
**shared** layer (PR a rule into the repo). No new mechanism.

### Phasing — small first PR

PoC ≈ 1 day of submodule work, validates direction without committing
to embeddings:

1. Move `agent_*.json` + `_module_map.json` writes to
   `~/.vc-context/<repo-hash>/index/`.
2. Update `agent_map.py` + MCP loader to read from the new path.
3. Add `agent_*.json` and `_module_map.json` to `.gitignore` of the
   parent project.
4. Add CI step `vc-context build --ci` (rebuilds local index in CI
   environment so MCP-aware checks still work).
5. Implement `find_similar_to(text)` — fuzzy first (Phase A), no
   embeddings.
6. Implement `record_decision` + `decisions.jsonl` storage.

**Does NOT include in PoC**: embeddings, replay, promotion CLI,
cross-machine sync. Those land only after the first PoC shows real
agents using the surface.

### Open questions before slicing PoC

- Embeddings: local Ollama vs one-shot Claude API at index time?
  Cost vs offline-friendliness tradeoff.
- CI flow: do we keep snapshot tests of the index shape that
  currently rely on committed `agent_*.json`? (Check `.ai-context`'s
  own test suite before the move.)
- First target repo for dogfooding: klodchickknifes (local) +
  vc-context-builder self-index (CI).

---

## ❌ Out of scope (won't build)

- True call-graph (cross-file). `who_calls` / `get_callees` are
  heuristic by design — keeps the build under 2s.
- LLM-generated summaries. We extract verbatim docstrings; we don't
  paraphrase.
- Test coverage / hot-path metrics. Dedicated tools (coverage.py,
  py-spy) do this better.
- Refactor sandbox. Read-only outside `git commit`.

---

## Compatibility

- **Python**: 3.9 minimum (lowest realistic dev runtime), CI tests
  3.11 + 3.13.  No external runtime deps (stdlib only).
- **MCP protocol**: 2024-11-05.
- **Indexer artefacts**: stable JSON shape; new optional fields are
  additive, never breaking.

---

## Versioning

Loose SemVer with CalVer-ish minor bumps.  Major version bump only on
breaking artefact / MCP-tool removal.  See `CHANGELOG.md` (when it
lands — see Planned above).
