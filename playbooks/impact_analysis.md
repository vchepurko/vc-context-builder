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

1. **Anchor on the target symbol.**
   `find_symbol(name="add_admin", fields=["file","line","kind","role"])`
   → confirm it exists, get its role for prioritising callers.

2. **Forward dependency surface — what does it touch?**
   `get_callees(symbol="add_admin")`
   → if it calls into the DB layer / external services, the change may
   need migration coordination.
   `get_raised_exceptions(symbol="add_admin")`
   → exception contract — if you change the surface, callers' except
   clauses might break.

3. **Reverse dependency surface — who depends on it?**
   - For Python symbols: `who_calls(symbol="add_admin")` (file-level
     heuristic) AND `find_call_sites(callable="add_admin")` (line-level
     reverse lookup).
   - For HTTP routes: `route_callers(path="/api/admin/staff/admins")`
     → returns JS + Python call sites.
   - For aiogram callbacks: `find_callback(data="...")`.

4. **Test coverage of the surface.**
   `find_test(symbol="add_admin")` → direct test.
   `coverage_for_role(role)` if the symbol's role has gaps.
   No test → flag as risk *before* listing the impact.

5. **Inspect each high-risk caller** — only the ones that look
   substantial. Use `find_symbol(name=..., fields=["file","line",
   "end_line"])` then `read_slice` for the relevant block.
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
