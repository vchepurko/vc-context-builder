---
description: Estimate the blast radius of changing an Angular symbol — components, services, templates, tests touched
argument-hint: <SymbolName>
allowed-tools: mcp__vc-context__find_symbol, mcp__vc-context__ng_audit_component, mcp__vc-context__ng_uses_selector, mcp__vc-context__ng_inject_graph, mcp__vc-context__find_call_sites, mcp__vc-context__find_test, mcp__vc-context__inspect_class
---

"How big is this change?" — surface every touch-point of Angular
symbol `$ARGUMENTS` so the user can decide PR size and migration
strategy before starting.

Workflow:
1. **Classify:** call `find_symbol($ARGUMENTS)`. Branch on `role`:
   * `ng-component` → run `ng_audit_component`, `ng_uses_selector`,
     `find_call_sites` (programmatic refs).
   * `ng-service` → run `inspect_class` (public methods),
     `ng_inject_graph` (DI sites), and `find_call_sites` per public
     method.
   * `ng-directive` → mostly like component (selector + usages), but
     skip `ng_audit_component` (audit returns null for non-component).
   * `ng-pipe` → grep templates for ` | <pipe_name>` via
     `find_in_templates`.
   * `ng-guard` → call `find_call_sites($ARGUMENTS)` — guards live in
     route configs, not constructors.
   * Anything else → fall back to `find_call_sites` and let the user
     judge.
2. **Tests:** `find_test($ARGUMENTS)`.
3. **Aggregate counts:** templates touched, TS files touched, distinct
   modules/folders touched, total tests covering the symbol.

Output shape:
```
## $ARGUMENTS — impact (`<role>`)

**File:** `<path>`

| Surface | Count |
|---|---|
| Templates referencing | N |
| TS files referencing  | M |
| Distinct directories  | D |
| Tests guarding        | T |

## Recommendation
- **Small change** (< 5 templates, < 10 TS files): single PR.
- **Medium** (5–20 templates, < 30 TS): split into mechanical
  rename + behaviour change.
- **Large** (> 20 templates or > 30 TS files): adapter pattern —
  add new alongside old, deprecate, migrate, delete.

## Next commands
- `/ng-audit-component <Name>` for a deeper component view
- `/ng-trace-service <Name>` for service-specific drill-down
- `/ng-find-selector <sel>` to enumerate the templates
```

Constraints:
- Use the *narrowest* tool first (typed lookups), fall back to
  `find_call_sites` only when the role isn't covered by a specific
  tool — typed lookups are 5–10× faster than substring scans.
- Don't list every hit — only counts and the recommendation. Send the
  user to `/ng-audit-component` / `/ng-find-selector` for the lists.
- If `find_symbol` returns null, suggest re-running with a different
  case (kebab-case selector → CamelCase class name) and stop.
