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
- **MCP server** (~47 tools)
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
    `lint_violations`, `mypy_violations`, `ruff_violations`,
    `check_health` roll-up)
  - File-level grep (`find_in_file`) — surgical single-file search
  - Anti-pattern detection (`find_orphan_callbacks`) — dead-button
    finder for aiogram projects
  - Markdown navigation: `get_doc_toc`, `find_doc_section` (anchor /
    number / heading / fuzzy selectors), `list_docs`, `find_doc_xref`,
    `search_doc_text` (markdown-aware grep with section context),
    `docs_link_graph`
  - Angular: components / routes / inject-graph / overview
  - Locales: list / find / get
- **`run_check` caching keyed on git state** — repeat invocations
  with no source edits return in ~ms with `cached: true`; saves
  10–20 s on `test-unit`.
- **`_QuerySymbolsMixin` extraction** — `query_engine.py` 1923 → 1271 LOC
  by hoisting 14 symbol-fact methods + 5 class constants into the new
  `_query_symbols.py`. Pure refactor, public surface unchanged.
- **CLI** (`vc-context`) — full parity with the MCP surface
- **JSON fallback** — read artifacts directly when no MCP / CLI
- **`include_tests` knob (default false)** on search/query tools —
  `find_symbol` / `find_symbols` / `who_calls` / `find_call_sites` /
  `find_callback` / `get_decorated_with` / `find_orm_field_usage`
  hide test-file matches by default. Production "where is X used?"
  queries no longer mix in test fixtures; coverage audits opt in
  explicitly. Helper: `_test_filter.py::is_test_path` (paths under
  `tests/` and `.ai-context/tests/`).

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

> **Recently shipped (2026-05-14)** — batch 1: `_QuerySymbolsMixin`
> split, `check_health` roll-up, `find_doc_section` loose selectors,
> `search_doc_text`, `find_orphan_callbacks`, `find_in_file`,
> git-state-keyed `run_check` caching. Batch 2: `find_locale_drift`,
> `find_handlers_without_tests`, TypeScript `interface`/`type`
> indexing, cross-language `inspect_class` (TS fall-through). Their
> planned bullets below stay as historical context; see CHANGELOG.md
> for the per-tool record.

### Code quality
- ~~Continue `query_engine.py` split — extract `_QuerySymbolsMixin`.~~
  ✅ Shipped 2026-05-14 — 1923 → 1271 LOC; new `_query_symbols.py`.
- Replace inline `import re` / `import ast` with module-level imports
  where the perf benefit is negligible.

### Telemetry & quality detectors — observations from real sessions

A long klodchickknifes session (2026-05-12, IDEAS #27 + #28 + Phase
3 — ~70 file edits, 56 MCP calls, 4633 tokens) surfaced concrete
gaps in the telemetry surface:

- **Bash usage isn't tracked.** `get_session_metrics` reports only
  MCP calls + heuristic baseline, so the "true MCP win" is
  understated. The session showed a 44.5% baseline win, but with
  Bash-grep usage counted (sed bulk-replaces, `grep -rn` for
  free-text, `node --check` for JS) the savings ratio would be more
  representative. Two paths:
  - **Light**: emit a single "session.summary" record on shutdown
    with a self-reported Bash count (claude-side instrumentation,
    not MCP-side). Cheap, requires agent cooperation.
  - **Heavier**: add a `hint_bash_use(action, bytes_estimate)`
    no-op tool that agents call when they shell out, so metrics see
    the volume. Coupling cost is the agent has to remember.
  Pick light first; revisit when 3+ users instrument.

- **Hot-reread detector caught real waste.** `run_check("test-unit")`
  fired 15× in the session — detector flagged "consider caching".
  The agent batched poorly: ran tests after almost every small edit
  instead of grouping 3-5 edits per test run. Two affordances we
  could add:
  - **Test-discriminator**: cache the last `run_check("test-unit")`
    result keyed on `git diff --stat` hash. If no source change
    since last run, return the cached summary with `cached=true`
    flag. Saves 10-20s per redundant invocation.
  - **Sugar in tool description**: explicit phrasing like
    "run only after 3+ edits accumulate" in the `run_check`
    description. Documentation wins behavior more than enforcement.

- **`mypy_violations` + `lint_violations` are dirt cheap but called
  reflexively in pairs.** 11 calls of each in the session; almost
  always empty. Could either:
  - Surface a single combined `check_health()` tool that returns
    `{lint: [...], mypy: [...], ruff: [...]}` in one call — 3 calls
    → 1 call, ~3× fewer round-trips.
  - Or document the existing tools as "use after batches, not after
    each edit; pre-commit hook covers per-commit". Behavioral.

- **Quality finding `wasteful_pairs` showed empty in this session**
  (good — no `find_symbol → grep -n` followed by Read of the same
  symbol). The rule that "don't grep after MCP answered" is
  internalised. Worth verifying on other agents'/users' sessions
  whether the detector ever fires; if not in 30 days, consider
  promoting it to a stricter `warn` or removing.

### Markdown / docs navigation — real gap

Same 2026-05-12 session: ~30% of edits touched markdown files
(`IDEAS.md`, `ROADMAP.md`, `docs/ENV.md`, `docs/OPS.md`,
`WEBAPP_V2_SPEC.md`, plus this submodule's own `ROADMAP.md`).
**Zero MCP coverage for that work** — `find_symbol` /
`find_by_role` / `summarise_module` are all Python-AST-only.
Result: I fell back to `grep -rn "^## "` + `tail` + `Read` to
locate sections, which the user flagged as "you're not using MCP
here either".

The current rule ("free-text inside file bodies → grep") is
correct **only because there's no alternative**. Bash isn't
faster for markdown navigation — it's the only option. Adding
even a thin markdown index closes a 30% blind spot in the MCP
surface.

Concrete tool sketches, cheapest first:

- **`get_doc_toc(file: str)`** — return `[(line, level, header_text), ...]`
  for a `.md` file. Implementation: regex over `^#{1,6} ` lines, no
  AST. ~30 LOC. Saves a `grep "^## "` + Read combo.

- **`find_doc_section(file, header_pattern, *, fuzzy=True)`** —
  match a section by title and return `(start_line, end_line)` so
  callers feed it into `read_slice` for surgical reads. The
  workflow that today is "Bash `grep -n '^## Planned'`, then
  `Read offset=63 limit=30`" becomes one MCP call.

- **`list_docs(*, glob='**/*.md')`** — enumerate every `.md` in
  the project, returning `(path, top_header, section_count,
  byte_size)`. Useful for "where do we document X" kind of
  queries before zooming in.

- **`find_doc_xref(symbol_or_term)`** — full-text search across
  the docs only (separate from code grep), return
  `[(file, line, snippet)]`. Distinct from `find_symbol` since
  docs reference flags / env names / concepts, not Python
  symbols.

- **`docs_link_graph()`** — extract all `[text](relative-path.md)`
  links, build a directed graph, report broken links + dangling
  docs (referenced from nowhere). One-shot validator that doubles
  as agent-navigation primitive ("which docs link to BACKUP.md?").

**Index build cost.** Markdown sits well below Python in churn
volume; ~20 files in this repo, ~3 KB each. A scan-and-index pass
adds < 50 ms to ``agent_map.py``. Artefact file:
``agent_docs_index.json`` (~30 KB), parallel to the existing
``agent_symbols.json``.

**Priority**: medium. Less than `find_orphan_callbacks` (already
queued) and `find_handlers_without_tests`, but higher than the
``query_engine`` split refactor — that's pure code-quality, this
extends user-visible surface.

**Phase 1 status (2026-05-12): ✅ all five tools shipped** —
``get_doc_toc`` / ``find_doc_section`` / ``list_docs`` /
``find_doc_xref`` / ``docs_link_graph`` are live in the MCP
server and serving real queries.

### Markdown navigation Phase 2 — calibration after head-to-head trial

Same-day 3-paired benchmark (MCP vs grep on `IDEAS.md`) revealed
the shipped tools have sharper interfaces than grep for some
ad-hoc queries. The honest scoreboard:

| Query | MCP | grep | Verdict |
| --- | --- | --- | --- |
| Full TOC of `IDEAS.md` | `get_doc_toc` → 12KB structured (level/line/end_line/anchor) | `grep -nE '^#+ '` → 5KB raw, truncated | **MCP** (metadata + bounds) |
| "Find section #31" | `find_doc_section(anchor="31")` → `null` | `grep '^## 31\.'` → 1 line, 80B | **grep** (MCP needs full slug) |
| "Which docs mention IDEAS #27" | `find_doc_xref("IDEAS #27")` → `[]` | `grep -rln "IDEAS #27"` → 3 files | **grep** (xref index doesn't catch free text) |

**Gaps to close so MCP is a strict superset of grep for docs:**

- **`find_doc_section` — loose anchor lookup.** Today the slug
  must be exact. Accept alternative selectors:
  - ``number=31`` → finds `## 31. …` at level 2 (regex on prefix).
  - ``heading="Unified User"`` → case-insensitive substring match
    on heading text, ranked by match length.
  - ``anchor="31"`` → slug prefix match when the full slug isn't
    known. Falls back gracefully across all three.

- **`search_doc_text(query, *, file=None, regex=False, kinds=None)`**
  — new tool. Markdown-aware grep returning
  ``[{file, line, section: {heading, anchor, level}, snippet}]``
  instead of bare lines. Groups hits per section so the agent
  sees "this mention is inside Phase 2 of IDEAS #28" without a
  follow-up Read. ``kinds=['ideas','spec','roadmap','folder-rules']``
  scopes by doc role. Closes the "which docs mention X" free-text
  query class.

- **`find_doc_xref` — broaden index to free-text refs.** Today
  only markdown-link syntax `[#27](#27-...)` is indexed. Extend
  the indexer to catch patterned mentions:
  - ``IDEAS #\d+`` / ``AUTH_SPEC §\d+`` / ``ROADMAP Phase \d+``
  - ``BOOKING_SPEC §...`` / ``WEBAPP_V2_SPEC §...``
  Store alongside link-xrefs in ``agent_docs_index.json`` with a
  ``kind: 'reference' | 'link'`` discriminator. Closes the "where
  else is IDEAS #27 discussed" query without grep.

- **`list_doc_headings(file, *, level=2)`** — light TOC.
  ``get_doc_toc`` returns the full recursive tree (~12 KB on
  `IDEAS.md`). For 80% of "where is section X" queries an agent
  only needs top-level entries (~3 KB). One filter parameter,
  same code path.

- **`find_doc_heading(query, *, file=None, top=3)`** — fuzzy
  search over heading text via rapidfuzz / difflib. "find the
  section about unified user" → top-3 headings across all docs
  with score.

**Priority order:**

1. **`search_doc_text`** — biggest single win. Covers free-text
   queries that today drop to grep entirely. ~80 LOC, mostly
   reuse of the existing TOC walker to attach section context
   to each hit.
2. **`find_doc_section` loose lookup** — ~30 LOC wrapper on the
   existing tool. Adds `number=` / `heading=` / prefix matching.
3. **`find_doc_xref` broaden index** — touches
   ``markdown_index.py`` indexer (~50 LOC + ~5 KB artefact bump).
4. `list_doc_headings(level=N)` and `find_doc_heading(query)`
   are polish — useful but #1–3 close the major functional gap.

**Acceptance test:** rerun the 3-paired benchmark above — all
three rows must flip to "MCP wins or ties" after these changes.
Pin as ``tests/test_doc_tools_vs_grep.py`` so the regression
stays caught.

### Anti-pattern detectors

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

### Angular / TypeScript gaps — from lms-client session (2026-05-12)

Four concrete issues surfaced during an Angular 19 feature build
(`my-profile-page`, groups section). All four can be reproduced with
a freshly-indexed Angular project.

- **TS `interface` / `type` not indexed.** `find_symbol('SectionState')`
  → `null`. The TS/JS parser only captures `class`, `func`,
  `async-func`. Adding `InterfaceDeclaration` and `TypeAliasDeclaration`
  to the AST/regex extraction would close a 57.9% empty-ratio
  blind spot observed in `get_session_metrics` (20 calls, 60%
  empty — all due to TypeScript interfaces). Fix: extend
  `parsers/ts_js_parser.py` to emit `kind: "interface"` /
  `kind: "type"` records; add an opt-in `index_ts_types: true`
  flag in `conventions.json` so projects that don't need it pay
  nothing.

- **`ng_audit_component` selector is `null` for `standalone: false`
  + `templateUrl` components.** Even with `typescript_ast: enabled`,
  the selector field came back `null` for a standard Angular
  declaration-based component. Workaround today: `read_slice` the
  first 20 lines. Fix: ensure the AST path reads `selector` from
  `@Component({selector: '...'})` for non-standalone components
  regardless of whether `templateUrl` is present.

- **`inspect_class` is Python-only.** Called on a TypeScript class →
  `null`. For Angular, the most-wanted output is constructor
  injection params (= which services are wired) + public methods
  (= what the template can call). A thin TS variant that parses
  `constructor(private x: X)` and public method signatures via
  `ts_js_parser` would replace 3-4 manual `read_slice` calls per
  component audit.

- **`rebuild_index` always times out (120 s) when `typescript_ast:
  enabled`.** Node-per-file spawning takes ~50 ms × 500+ components
  ≈ 25 s+ on top of the normal build, pushing total past the MCP
  timeout. Fix options: (a) run the Node pass in a single batched
  worker process instead of per-file spawns; (b) expose an async
  `rebuild_index` that returns a job-id and a `poll_rebuild` tool;
  (c) increase the tool timeout to 300 s for rebuild only. Option
  (a) is the cleanest; (b) adds API surface but keeps the MCP
  contract synchronous everywhere else.

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
- **`find_in_file(file, pattern, *, limit=20, regex=False)`** —
  surgical grep over a single file with line numbers + context.
  Captures the "I know the file, I'm hunting a string inside it"
  case the JS files surface every session (`Checkout.js` is one
  big component, `find_symbol` only reaches the top-level class).
  Avoids the Bash fall-back that prompted "так може покращити для
  жс команду?" — 2026-05-11.
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
- **Sampled baseline benchmark** — every Nth call (configurable, e.g.
  1-in-20 or 1-in-30), the dispatcher also runs the equivalent Bash
  query (grep / Read) in parallel and emits a side-by-side telemetry
  record: token-budget MCP vs baseline, latency, result-set
  cardinality. Yields a rolling savings estimate without the
  full-time 2× cost of always-shadow mode (discussed in the
  klodchikknifes session — exact measurement is expensive; sampling
  trades precision for sustainability). Implementation: probabilistic
  shim in `mcp.dispatcher.call()` + a new `baseline_calls` sidecar
  next to the existing JSONL telemetry; `get_session_metrics` adds a
  `baseline` block to its output.

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
