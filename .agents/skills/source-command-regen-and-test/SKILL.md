---
name: "source-command-regen-and-test"
description: "Regenerate vc-context artefacts (agent_*.json) and run the submodule's own checks — used after a parser/builder change"
---

# source-command-regen-and-test

Use this skill when the user asks to run the migrated source command `regen-and-test`.

## Command Template

Run after editing any parser, indexer, builder, or MCP tool in this submodule. Two phases:

**Phase 1 — Regenerate artefacts** (from the parent repo root, since `agent_map.py` resolves paths relative to it):

```bash
cd /Users/vchepurko/projects/klodchickknifes && python3 .ai-context/agent_map.py
```

Capture the script's stdout summary. If it errors, **stop** and surface the traceback — no point running tests against a broken index.

**Phase 2 — Run the submodule's tests** (if a test runner is configured here):

1. Check `pyproject.toml` / `pytest.ini` inside `.ai-context/` for a test config.
2. If pytest config exists: `cd .ai-context && uv run pytest -q` (or fall back to `python3 -m pytest -q` if uv isn't set up here).
3. If no submodule tests: skip Phase 2 and say so.

**Phase 3 — Smoke-test the MCP surface** (lightweight): from the parent repo, call:
- `mcp__vc-context__list_modules()` — should return non-empty
- `mcp__vc-context__list_checks()` — should return the whitelist
- `mcp__vc-context__ruff_violations(summary=true)` — confirms ruff inspector still loads

Output:
- **Regen result**: passed / failed (with N modules indexed, N callbacks, N tests)
- **Submodule tests**: pass count / skipped if none
- **MCP smoke**: 3 quick checks pass / which failed
- **Diff vs HEAD**: `git diff --stat _module_map.json agent_*.json` from the parent repo, so the user sees what the regen actually changed
- End with: **next step** (commit the regen + your code change together, or fix the failure first)

Do NOT git add or commit anything — only report.
