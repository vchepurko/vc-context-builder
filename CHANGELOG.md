# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to a loose [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
— breaking changes bump the major; new MCP tools or artefact fields
bump the minor; fixes bump the patch.

## [Unreleased]

### Added
- **`vc-context handoff` CLI** — project-local handoff memory for switching
  between Codex, Claude, Gemini, Cursor, Aider, or subscribed chat accounts
  with minimal context loss. `handoff init` creates a root `HANDOFF.md`
  pointer and `.vc-context/HANDOFF.md`; `handoff snapshot` rewrites the task
  state with git status/diff, notes, blockers, and next step, and archives a
  copy under `.vc-context/handoffs/`; `handoff prompt` prints a copy-paste
  resume prompt for the next agent.
- **`run_checks` structured summaries via per-check `parser`** — a check
  in `.vc-context/conventions.json` (object form) may declare a
  `"parser"`. When set, `run_check` feeds the **full** command output to
  that parser and returns a structured object in `summary` instead of the
  pytest one-liner, so agents read `summary.failures` directly rather than
  grepping a truncated `stdout_tail`. Ships the `karma-jasmine` parser
  (`{suite, test}` per failed spec, nearest `describe` wins; plus
  `executed`/`total` when printed, and a `compileErrors` list of
  `ERROR in …` webpack/ts-loader lines so a green `failed: 0` can't hide
  type errors). Backward compatible: array-form checks
  and the pytest summary path are unchanged. Register new parsers in
  `checks.py` → `_SUMMARY_PARSERS`.
- **`semantic_search(query, top_k=5)`** — Phase 5 semantic symbol
  search backed by a per-repo local SQLite store at
  ``~/.vc-context/<repo-hash>/embeddings/symbols.sqlite``. The first
  provider is deterministic ``local_hash`` feature hashing:
  stdlib-only, offline, fast, and shaped so sqlite-vec / model-backed
  embeddings can replace it later without changing the MCP or CLI
  contract. CLI: ``vc-context semantic "course completion"``.
- **`remember_experience` / `recall_experience`** — Phase 5 repo-local
  experience store for decisions, mistakes, dead ends, and patterns.
  Writes to ``~/.vc-context/<repo-hash>/learned/experience.sqlite``;
  recall returns compact scored hits and marks file-anchored memories
  stale when the source file disappears. CLI:
  ``vc-context remember-experience`` / ``vc-context recall-experience``.
- **`find_local_agents_md(path)`** — walks up from ``path`` (file or
  directory) and returns every ``AGENTS.md`` along the way,
  most-specific first. Discovers folder-scoped invariants without a
  filesystem walk. Each record: ``{file, size_bytes}``. Walks stop
  at ``project_root``; the top-level ``AGENTS.md`` is the
  most-general entry.
- **`find_anti_patterns` registry** — new MCP tool plus
  `list_anti_patterns`. First detector:
  `aiogram-state-check-in-body` finds
  ``@router.message(F.<...>)`` decorators without a state-filter
  argument (the silent-dispatch killer pinned in CLAUDE.md). Pure
  AST set-difference, stdlib-only; further rules slot into the
  ``_DETECTORS`` registry as plain functions.
- **`record_bash_usage`** — light-touch self-reported Bash usage
  marker. Agents call it after a shell-out so the dispatcher's
  auto-record adds the entry to
  ``~/.vc-context/metrics/<repo>-<date>.jsonl``;
  ``get_session_metrics`` then surfaces Bash counts alongside MCP
  calls (``by_tool['record_bash_usage']``). Closes the "true MCP
  win is understated" blind spot in the ROADMAP.

### Fixed
- **`ng_eslint_violations` returned stale results after an edit** — the
  per-path cache was purely time-based (`_ESLINT_CACHE_TTL=300s`), so a
  re-lint within the window kept reporting violations the user had already
  fixed (and no way to bypass it). The cache entry is now keyed on a cheap
  file fingerprint (file count + latest mtime over `.ts`/`.html`/`.js`
  under the path); any add/edit/delete busts it automatically, with the
  TTL kept only as a secondary upper bound. Added a `nocache` parameter
  (tool schema + dispatcher) to force a fresh run on demand.
- **ng-component selector backfill** in `ts_js_parser` — long
  imports or extensive `@Component(...)` metadata used to push
  `selector` outside the primary regex path's 2 KB lookback
  window, returning ``null`` for non-standalone declaration-based
  components. New ``_backfill_ng_metadata`` does a no-window
  rescan of the full file body and recovers
  `selector` / `templateUrl` / `standalone` when missing.

### Changed
- **TS AST extractor uses a persistent Node worker** —
  `_ts_ast_extractor.mjs` gained a ``--server`` mode that reads
  file paths from stdin and emits one JSON line per file.
  `parse(file, project_root)` routes through one long-lived worker
  per project root, dropping the per-file cost from ~50 ms (spawn)
  to ~1–3 ms (AST parse only). Closes the lms-client
  "``rebuild_index`` always times out at 120 s with
  `typescript_ast` enabled" gap. One-shot fallback preserves the
  legacy contract for failed-worker / Node-missing cases.

- **`check_health`** — one-call code-health roll-up: `lint_violations` +
  `mypy_violations` + `ruff_violations` + `ruff_format` bundled into a
  single MCP round-trip. Real-session telemetry showed 33 calls split
  11/11/11 between the three inspectors, almost always empty — this
  collapses them. `summary=True` (default) keeps responses under
  ~250 B when the codebase is clean.
- **`find_doc_section` loose-lookup selectors** — `anchor="31"` (slug
  prefix), `number=31` (numeric heading prefix), `heading="Unified User"`
  (shortest-substring rank), plus the back-compat `header_pattern`
  path. Priority: anchor > number > heading > header_pattern. Closes
  the "I know the number, not the full slug" gap surfaced by the
  Phase 2 markdown calibration trial.
- **`search_doc_text`** — markdown-aware grep across indexed docs.
  Each hit carries `section: {heading, anchor, level}` so the agent
  sees "this mention is inside Phase 2 of IDEAS #28" without a
  follow-up `read_slice`. Closes the "which docs mention X"
  free-text query class that today drops to Bash `grep -rln`.
- **`find_orphan_callbacks`** — anti-pattern detector: every literal
  `callback_data="..."` reference with no matching
  `@router.callback_query` handler. Set-difference between
  AST-walked button references and the handler index; non-literal
  call sites (f-strings, `.format()`, variables) are silently
  skipped because they can't be statically resolved.
  `include_tests` defaults to false.
- **`find_in_file`** — surgical grep over a single file. Closes the
  "I know the file, I'm hunting a string inside it" case
  (`find_symbol` only reaches top-level shape; large monoliths like
  `Checkout.js` are the typical motivator). Path is sandboxed
  against `project_root`; files > 5 MB are skipped.
- **`run_check` caching keyed on git state** — repeat invocations
  with no source edits return in ~ms with `cached: true`. Hash
  covers committed HEAD + staged + unstaged + untracked files via
  `git status --porcelain`. Saves 10–20 s on `test-unit`. Pass
  `nocache: true` to bypass; caching skipped on spawn failures (-3)
  and on non-git projects.
- **`_QuerySymbolsMixin` extraction** — moved 14 methods +
  5 class constants out of `query_engine.py` (1923 → 1271 LOC) into
  a new `_query_symbols.py`. Pure refactor — public surface
  unchanged.
- **`find_locale_drift`** — parity audit: every locale key present in
  one language but missing in a sibling that owns the same namespace
  file. Returns ``[{key, namespace, present, missing}]`` sorted by
  ``(namespace, key)``. Drives translation review without a dedicated
  diff tool. Optional ``namespace`` scopes to one bucket.
- **`find_handlers_without_tests`** — coverage gap detector: every
  symbol with the given handler role (default ``aiogram-handler``)
  that has no linked test entry. Sugar over
  ``coverage_for_role(role)["missing"]`` enriched with ``line`` /
  ``kind`` so the agent can jump straight to source. Empty list =
  parity OK.
- **TypeScript `interface` + `type` indexing** in `ts_js_parser` —
  closes the 57.9% empty-ratio blind spot observed in real
  lms-client sessions (``find_symbol('SectionState')`` was
  consistently null because only ``class``/``func``/``async-func``
  were indexed). Adds two regex matchers; ``interface`` records
  carry ``kind: "interface"``, type aliases ``kind: "type"``.
- **`inspect_class` cross-language fall-through** — TypeScript /
  TSX classes now resolve to the same ``{name, file, line, doc,
  bases, fields, methods}`` shape Python classes already do.
  ``bases`` covers ``extends`` + ``implements``; ``fields`` includes
  ``@Input`` / ``@Output`` / constructor DI params (tagged via
  ``kind``); ``methods`` lists public methods (lifecycle hooks and
  ``_``-prefixed members skipped). Replaces 3-4 manual
  ``read_slice`` calls per Angular component audit.
- **`include_tests` knob on search/query tools** (default false). Hides
  test-file matches from `find_symbol` / `find_symbols` / `who_calls` /
  `find_call_sites` / `find_callback` / `get_decorated_with` /
  `find_orm_field_usage` so "where is X used?" and "where is X
  defined?" answers stop mixing production hits with their test
  fixtures. Pass `include_tests: true` to opt back in for coverage
  audits. Helper lives in [`_test_filter.py`](_test_filter.py); 12
  new tests in [`tests/test_test_filter.py`](tests/test_test_filter.py).
- **`ARCHITECTURE.md`** — text-diagram walk-through of the
  three-layer design (indexer → engine → surfaces).
- **`CONTRIBUTING.md`** — dev setup, hooks, conventions, how to
  add an MCP tool / parser.
- **`Makefile`** — `make test` / `make lint` / `make ci` /
  `make demo` / `make install-hooks` / etc.
- **Targeted JSON parser** (`package.json` / `tsconfig.json` /
  `composer.json`) — was a 0-byte placeholder.
- **Method-level decorators** — class `decorators` field now folds
  in `@staticmethod` / `@property` / `@abstractmethod` from method
  bodies, so `get_decorated_with("staticmethod")` finds the class.
- **README "Demo" section** — real CLI output for card / repo-map /
  slice / stats.
- **README TL;DR block** — 4-line install + first call.
- 5 new test files (file_parser, symbols, mypy_inspector,
  ruff_format_inspector, agent_map integration) — covers ~750 LOC
  previously untested.

### Changed
- **`mcp/specs.py` (959 LOC) split** into the `mcp/specs/` package
  (`symbols.py` / `project.py` / `angular.py`).
- **`cli.py` (825 LOC) split** into `cli.py` (entry + argparse) +
  `cli_handlers.py` + `cli_renderers.py`.
- **`USAGE.md` trimmed** — dropped recipes that overlap with
  CONTRIBUTING / MCP_SETUP / README.
- **`bin/vc-context` UX** — friendly nudge to run `vc-context build`
  when `agent_root.json` is missing, instead of a raw stack trace.
- `logging.basicConfig` moved from `agent_map.py` module level to
  the `__main__` block (no library-side root-logger mutation).
- `parsers/get_parser` typed as `Optional[BaseParser]` (was lying
  to type-checkers about the missing-parser case).
- ROADMAP rewritten to reflect actual shipped vs planned vs deferred.
- `SERVER_VERSION` bumped `0.1.0` → `0.5.0`.

### Fixed
- 19 pre-existing mypy errors → 0 (annotations, casts, type-guards).
- 1269 pre-existing ruff baseline → 0 (auto-fix + targeted manual).
- `assert isinstance` narrowing replaced with `if not isinstance`
  fallthrough (survives `python -O`).

### Removed
- Empty `parsers/json_parser.py` placeholder (replaced with
  the real targeted parser).
- Empty `tests/test_json_parser.py` placeholder (replaced with
  real tests).
- `AGENT_README.md` from git tracking — auto-generated, now
  in `.gitignore`.

### Planned (next PR)
- Continue `query_engine.py` split — extract a `_QuerySymbolsMixin`
  for the remaining symbol-cluster (~600 LOC). Three mixins already
  landed (inspectors / routes / tests); facade is at 1507 LOC.

See [`ROADMAP.md`](ROADMAP.md) for the full deferred / planned list.

---

### Added (this PR — verify + mixin split)
- **Typed `verify(kind, subject, target?)`** MCP tool + CLI
  subcommand. Four kinds: `exists`, `calls`, `decorated` (suffix-
  aware), `raises`. Returns `{result: bool, evidence: str}` for
  one-call fact-check without reading the body.
- 13 new tests covering each kind + edge cases.

### Changed (this PR — verify + mixin split)
- **`query_engine.py` 1836 LOC → 1507 LOC** via three mixins:
  - `_query_inspectors.py` (234 LOC) — locales / notify / ruff /
    mypy / format / telemetry.
  - `_query_routes.py` (122 LOC) — HTTP routes / Angular routes /
    aiogram callbacks / FSM flow.
  - `_query_tests.py` (208 LOC) — find_test / coverage / classify /
    tests_by_category.
  - `QueryEngine` is now `class QueryEngine(_InspectorsMixin,
    _RoutesMixin, _TestsMixin)`. Public API unchanged — every test
    that imports `QueryEngine` keeps working through MRO.

---

## [0.5.0] — 2026-05-08

The "production polish" release.  Code-quality baseline + dev tooling
brought to a point where the submodule can be shared with other devs
without embarrassment.

### Added
- **MIT [LICENSE](LICENSE)** — was missing despite README referencing it.
- **Targeted JSON parser** — extracts `package.json`, `tsconfig.json`,
  `composer.json` shape (name + version + dependencies / paths /
  requires).
- **Pre-commit hooks** (`.pre-commit-config.yaml`): ruff + format +
  mypy + tests + snapshots + standard sanity hooks.
- **CI lint workflow** (`.github/workflows/lint.yml`): ruff check +
  format + mypy + snapshot drift, gating.
- **Quality findings** (`get_session_metrics(quality=true)`):
  `wasteful_pairs`, `hot_rereads`, `empty_streaks` detectors over
  the telemetry sidecar.
- **Per-call telemetry sidecar** — JSONL log to
  `~/.vc-context/metrics/<repo>-<date>.jsonl`. CLI: `vc-context stats`.
- **Card-shaped MCP tools** — `get_symbol_card`, `get_file_card`,
  `repo_map`, `get_changed_symbols`, `get_decorated_with`.
- **Tier-1 evidence tools** — `get_callees`, `get_raised_exceptions`,
  `read_slice` (path-traversal-guarded).
- **AST decorator capture** — `decorators: [...]` on every Python symbol.
- **Line ranges** — `line` / `end_line` on every symbol record.
- **`fields=` whitelist** + `include_body=true` + `find_symbols`
  batch on `find_symbol`.
- **Task-shaped playbooks** — `bug_investigation`, `impact_analysis`,
  `refactoring_review` recipes.
- **`AGENTS.md`** at parent project root — cross-tool agent entry.

### Changed
- **mypy clean** — resolved 19 pre-existing errors → 0 in src tree.
- **ruff clean** — resolved 1269 baseline errors → 0 (auto-fix where
  safe, manual targeted fixes for `RUF012` / `B007` / `F841` / `N806`
  / `W293` / `E501`).
- **`logging.basicConfig` moved into `__main__` block** of `agent_map.py`
  — bibliothèque-side root-logger mutation was an anti-pattern.
- **ROADMAP rewritten** to reflect actual shipped vs planned vs
  deferred state.
- **`SERVER_VERSION` bumped** `0.1.0` → `0.5.0` to reflect real
  surface size.
- **`pyproject.toml` added** with conservative ruff config
  (target=py39, `keep-runtime-typing=true`, ignored noisy rules).

### Fixed
- **Format drift** in `agent_map.py` after late edit.
- **`get_parser` return type** — was `BaseParser`, now `Optional[BaseParser]`
  (no longer lies to type-checkers about missing-parser case).

---

## [0.4.0] — pre-CHANGELOG

Initial public-facing surface. Roughly:
- AST-based Python parser; heuristic JS / TS / PHP / DevOps parsers.
- MCP server with ~30 tools (find_symbol, who_calls, find_call_sites,
  inspect_class, route_callers, find_callback, trace_fsm_flow,
  classify_tests, locale queries, lint / mypy / ruff inspectors,
  whitelisted check runner).
- CLI `vc-context` with full parity.
- `_module_map.json` per folder + `agent_root.json` /
  `agent_symbols.json` / `agent_tests.json` artefacts.
- Convention linter, custom roles, route bridge (JS ↔ Python).
- Angular metadata extraction (regex + optional TypeScript-AST upgrade).
- File-level parse cache.

History before 0.5.0 was kept in git only — see `git log` for the
full chronology.

[Unreleased]: ../../compare/v0.5.0...HEAD
[0.5.0]: ../../releases/tag/v0.5.0
[0.4.0]: ../../releases/tag/v0.4.0
