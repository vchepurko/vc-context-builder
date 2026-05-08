# Refactoring review playbook

## When to use

- Reviewing your own (or someone else's) refactor branch / PR before
  merge.
- Sanity-checking a "no behaviour change" claim.
- Deciding whether to ship one bundled refactor or split into smaller
  PRs.

## When NOT to use

- Feature PR with new behaviour — review by reading the diff + tests,
  not by impact-mapping every touched symbol.
- Trivial typo / formatting PRs.
- A refactor where the diff is purely renames inside one file — git
  shows it; no MCP value.

## MCP sequence (typical)

1. **List the symbols actually touched — one call.**
   `get_changed_symbols(base="main")`
   → `[{name, file, line, end_line, kind, role?}, ...]`. Replaces
   the old "git diff hunks → match against find_symbol" dance with
   a single MCP call. Skip the playbook entirely if the list is
   empty (pure formatting / docs / config changes).

2. **Per touched symbol — full picture in one call.**
   For each `S`:
   - `get_symbol_card(symbol=S)` → bundles `raises` (contract drift),
     `callees` (new external deps?), `test` (coverage), and
     `callers` summary (signature-change blast radius).

   Compare against expectations:
   - Exception contract changed? PR description should say so.
   - New callees that weren't there before? New dependency edge —
     review-worthy.
   - `card.test` is null? Coverage gap. Flag.

3. **Per touched symbol — check callers still hold.**
   When `card.callers.total` is high OR signature changed in the diff:
   `find_call_sites(callable=S)` for the full line-level list.
   Spot-read 1–2 with `read_slice`.

4. **Run the project's check suite, don't trust intuition.**
   `run_check("test")`, `run_check("lint")`, `run_check("typecheck")`
   — these are the project's truth.  Refactor "without behaviour
   change" that fails any of these is a red flag.

5. **If lint / type errors exist** — narrow down:
   `lint_violations()`, `mypy_violations(summary=true,
   path_prefix="...")`, `ruff_violations()` — pinpoint the regressions.

## Context budget

- **Hard cap: 3 spot-reads** of touched symbol bodies.
- Don't read the test file — `find_test` tells you it exists; the
  check suite (step 4) tells you it passes. Only open the test if
  it's failing or missing.
- If `git diff` is >500 lines: don't summarise file-by-file. Group by
  role (api-client, repository, handler, …) and report distribution.

## Evidence rules

- "Behaviour preserved" requires the test suite passing AND no
  exception-contract drift on touched symbols. Don't claim
  preservation from diff inspection alone.
- "Caller-safe" requires either (a) signature unchanged, OR (b) every
  caller updated in the same PR. Cite the call sites you checked.
- Lint / type / format check failures → list with file:line:rule.
  Don't say "looks clean" without running the checks.

## Output format

```
Refactor scope:  <N symbols across M files>
Exception contract drift: <none | list (symbol: before → after)>
Caller safety:   <signature unchanged | N callers updated | RISK: <list>>
Test coverage:   <covered | MISSING for: <symbols>>
Check suite:     <test ✓/✗, lint ✓/✗, typecheck ✓/✗, format ✓/✗>
Recommend:       merge | split | block (with reason)
Confidence:      high | medium | low
```

## Failure mode

- PR touches symbols not in the index → `get_changed_symbols`
  silently drops them (returns only indexed ones). Causes: index out
  of date OR new file not yet under a scanned module. Run
  `python3 .ai-context/agent_map.py` and re-check before judging.
- `get_changed_symbols` returns `[]` AND you know there are changes →
  not in a git repo OR diff failed silently. Fall back to
  `git diff --stat` (Bash) to confirm the diff is real.
- `run_check` not configured for the project → state that explicitly;
  don't guess the test outcome.
