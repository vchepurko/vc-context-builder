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
- **MCP server** (~40 tools)
  - Symbol cards (`get_symbol_card`), file cards (`get_file_card`),
    repo map (`repo_map`)
  - Reverse / forward call lookup (`who_calls`, `get_callees`,
    `find_call_sites`)
  - Decorator search (`get_decorated_with`)
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

### Bigger query primitives
- **Typed `verify(kind, …)`** — fact-check primitive over existing
  indexes: `verify("calls", a, b)` / `verify("decorated", sym, dec)` /
  `verify("raises", sym, exc)` / `verify("exists", sym)`. Lets agents
  prove a claim without reading the body.

### Code quality
- **Split `query_engine.py` (~1800 LOC)** into per-domain mixins
  (`_symbols.py` / `_project.py` / `_routes.py` / `_runtime.py`).
  Mechanical refactor; risk-controlled by the existing 510-test
  suite. Worth a dedicated PR — a rushed split risks behavioural
  drift on the central engine.
- Replace inline `import re` / `import ast` with module-level imports
  where the perf benefit is negligible.

---

## 🤔 Deferred — needs signal before it's worth building

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
