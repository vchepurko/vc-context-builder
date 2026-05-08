# Impact analysis playbook

## When to use

- "What breaks if I change X?" / "Who depends on this function?"
- Before renaming, removing, or changing the signature of a symbol.
- Estimating the blast radius of a refactor.

## When NOT to use

- "Why does X fail?" — that's [bug_investigation](bug_investigation.md).
- Trivial local edits (variable rename inside one function) — no
  cross-file impact, no need for the playbook.
- "Will this scale?" — performance question, not impact.

## MCP sequence (typical)

1. **Anchor + forward surface in one call.**
   `get_symbol_card(symbol="add_admin")`
   → file/line/end_line, role, **callees**, **raises**, linked test,
   callers summary (capped at 5 — see step 3 for the full list).
   This single response covers what used to take 4-5 lookups:
   - `card.callees` → forward deps (DB layer, external services?)
   - `card.raises` → exception contract (callers' except clauses
     break if you change this)
   - `card.test` → coverage; missing test → flag *before* listing
     impact
   - `card.callers.total` → blast-radius hint; full list in step 3.

2. **Bonus checks for special symbol kinds.**
   - HTTP route handler? `route_callers(path="/api/admin/...")` —
     returns JS + Python call sites in one shot (the card's caller
     summary is Python-only).
   - aiogram callback? `find_callback(data="...")`.
   - Otherwise skip and go to step 3.

3. **Full reverse-dependency list (when card.callers.total is high).**
   - `find_call_sites(callable="add_admin")` — line-level reverse
     lookup, includes `match_path` filter.
   - `who_calls(symbol="add_admin")` — file-level heuristic.
   - For role-wide gaps: `coverage_for_role(role)`.

4. **Inspect each high-risk caller** — only the ones that look
   substantial. Use `find_symbol(name=..., fields=["file","line",
   "end_line"])` then `read_slice` for the relevant block. Or, if
   the caller is non-trivial, `get_symbol_card(name)` to peek at its
   own callees/raises before reading.
   **Stop after 3 caller inspections** unless the caller list is short.

## Context budget

- **Hard cap: 3 source reads.** This task is breadth-first by design;
  reading deep into every caller defeats the purpose.
- Prefer `who_calls` + `find_call_sites` over reading source — file
  paths + line numbers are usually enough to estimate impact.
- If the caller list is >20 → don't enumerate. Group by role / file
  prefix and report that distribution.

## Evidence rules

- "X is called from N places" — back with the actual list (or top 5 +
  count).
- "Changing the signature breaks Y" — cite the call site
  (`file:line`).
- Mark callers you didn't read as "(call-site, body unread)" so the
  reader knows the claim is structural, not behavioural.

## Output format

```
Symbol:        <name>  (file:line, role)
Forward deps:  <count callees, top N>
Reverse deps:  <count callers, by role/file>
Test coverage: <test file or "MISSING">
Risk hotspots: <call sites needing manual review>
Suggested order:
  1. <step>
  2. <step>
Confidence:    high | medium | low
```

## Failure mode

- `who_calls` empty AND `find_call_sites` empty → either dead code
  (worth flagging) or import-time-only usage that the heuristic
  misses. Suggest a `grep` confirmation as a fallback.
- Symbol lives in a generated file (`alembic/versions/*`,
  `_module_map.json`) → impact-analysis isn't meaningful, return that
  observation instead of pretending to analyse.
