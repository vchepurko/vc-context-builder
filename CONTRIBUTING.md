# Contributing to vc-context-builder

Thanks for taking the time. The codebase is **stdlib-only** by design
— please don't add runtime dependencies without discussing it in an
issue first.

## Local setup (one-time)

```bash
# Clone (or you already have it as a submodule)
git clone https://github.com/<you>/vc-context-builder
cd vc-context-builder

# Install the pre-commit hooks. They mirror the CI gating workflow,
# so passing them locally means CI will pass too.
pip install pre-commit  # or: uv tool install pre-commit
pre-commit install
```

The hooks use `uv run --with <tool>` for ruff and mypy — no need to
install either of them globally.

## Running checks

```bash
# Tests — pure unittest, no pytest required.
python3 -m unittest discover tests

# Lint (auto-fix safe rules) + format check.
uv run --with ruff ruff check . --fix
uv run --with ruff ruff format .

# Type-check.
uv run --with mypy mypy . --ignore-missing-imports

# Snapshot drift check (catches MCP tools/list mismatches).
python3 tests/regen_snapshots.py --check
```

Or run them all at once via the project Makefile:

```bash
make ci
```

## Adding an MCP tool

The dispatch surface is two halves that the parity test enforces stay
in sync — if you change one, change the other.

1. **Engine method** — add `def my_tool(self, ...)` on
   `QueryEngine` in [`query_engine.py`](query_engine.py) (or wherever
   the domain logic lives).
2. **Dispatcher handler** — add `_my_tool` on `Dispatcher` in
   [`mcp/dispatcher.py`](mcp/dispatcher.py) and register it in
   `self._handlers`.
3. **Spec entry** — append a JSON-Schema record to
   [`mcp/specs.py`](mcp/specs.py) `tool_specs()`.
4. **Tests** — at least one happy-path test. Drop it in
   [`tests/`](tests/) following the `test_<module>.py` convention.
5. **Snapshot** — regenerate the tools/list fixture:
   ```bash
   python3 tests/regen_snapshots.py
   ```

The dispatcher↔spec parity test in
[`tests/test_mcp_server.py`](tests/test_mcp_server.py) will fail if
either half is missing — use it as a checklist.

## Adding a parser for a new language

1. Create `parsers/<lang>_parser.py`. Subclass `BaseParser`, declare
   `extensions = (...)` and / or `filenames = (...)`. Implement
   `extract(file_path)` returning `{"exports": [...], "dependencies": [...]}`.
2. The `parsers/__init__.py` auto-registry picks it up on import.
3. Add tests under `tests/test_<lang>_parser.py`.

See the existing parsers (`python_parser.py`, `ts_js_parser.py`,
`json_parser.py`, `php_parser.py`, `devops_parser.py`) for the
conventions.

## Conventions enforced by ruff

The lint config in [`pyproject.toml`](pyproject.toml) ignores rules
that are stylistic-only or false-positive heavy in this codebase. The
ones that **stay enforced** are the ones that catch real bugs:

- Pyflakes (`F`) — undefined names, unused imports, etc.
- pycodestyle (`E`/`W`) — actual style issues, not preferences.
- bugbear (`B`) — likely bugs (`B007` unused loop var, etc.).
- pyupgrade (`UP`) — except `UP006`/`UP035` (would require
  `from __future__ import annotations` everywhere).
- naming (`N`) — only the genuine cases (no enforcement-by-fashion).
- ruff-specific (`RUF`) — except RUF001/2/3 (Cyrillic in docstrings
  is intentional).

If you want to add a new rule to the enforced set, drop a line in
`pyproject.toml` `[tool.ruff.lint] select = [...]`.

## Commit messages

Loose conventional-commits style — prefixes that show up in the log:

- `feat(<area>):` — new functionality
- `fix(<area>):` — bug fix
- `chore:` — packaging, deps, infrastructure
- `docs:` — README / playbook / changelog updates
- `style:` — pure formatting passes (no behaviour change)
- `test:` — test additions / refactors
- `ci:` — workflow / pre-commit changes
- `refactor:` — internal reorganisation, same behaviour

Don't worry about strict conformance — the prefix helps when scanning
`git log --oneline`, that's all.

## Pull requests

- One concern per PR. A "fix bug X + refactor Y + add feature Z" PR
  takes 3× longer to review than three separate ones.
- The CI workflows must be green (test + lint). Pre-commit catches
  most issues locally.
- Mention the playbook / use case the PR enables when relevant.

## Issues

- Reproductions go a long way. A 5-line snippet that triggers the bug
  is worth more than three paragraphs of description.
- Performance / token-economy claims should be backed by
  `vc-context stats --quality` output where possible.

## License

MIT. By contributing, you agree your changes ship under the same
license. See [`LICENSE`](LICENSE).
