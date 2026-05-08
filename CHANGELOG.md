# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to a loose [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
— breaking changes bump the major; new MCP tools or artefact fields
bump the minor; fixes bump the patch.

## [Unreleased]

### Planned
- Typed `verify(kind, …)` fact-check primitive
- Method-level decorator capture
- TL;DR block at top of README
- `query_engine.py` split into per-domain modules

See [`ROADMAP.md`](ROADMAP.md) for the full deferred / planned list.

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
