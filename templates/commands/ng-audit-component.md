---
description: Full pre-refactor audit of an Angular @Component — selector, template, inputs/outputs, services injected, tests, template usages
argument-hint: <ComponentClassName>
allowed-tools: mcp__vc-context__ng_audit_component, mcp__vc-context__ng_uses_selector, mcp__vc-context__inspect_class, mcp__vc-context__find_call_sites, mcp__vc-context__ng_inject_graph, mcp__vc-context__find_test, Read
---

Audit the Angular component `$ARGUMENTS` before changing it. Surface
its public contract, where it's used, what it injects, and what tests
guard the behaviour.

Workflow:
1. **Skeleton:** call `ng_audit_component($ARGUMENTS)`. Pulls
   `{file, selector, template_url, standalone, inputs, outputs,
   style_urls, doc, test}` in one shot.
2. **Class shape:** call `inspect_class($ARGUMENTS)` for the constructor
   parameters (this is how Angular DI is wired) and the public methods
   (anything reachable from a template).
3. **Template usage:** when the audit returned a `selector`, call
   `ng_uses_selector(selector)`. The result lists every HTML template
   that hosts this component as `<sel>` or `[sel]`. If empty,
   the component is dead-code or used only programmatically.
4. **Caller files (TS/JS):** call `find_call_sites($ARGUMENTS)` to catch
   programmatic references — `ViewChild`, `imports: [...]` arrays,
   `providers: [...]` arrays, dynamic component creation.
5. **Tests:** if `ng_audit_component.test` was null, also try
   `find_test($ARGUMENTS)` directly — the index is sometimes stale; a
   live filename scan can find a `.spec.ts` it missed.

Output shape:
```
## $ARGUMENTS — audit

**File:** `<path>`
**Selector:** `<sel>` (standalone: yes/no)
**Template:** `<templateUrl or "inline">`
**Inputs:** [...]
**Outputs:** [...]
**Injected services:** (from constructor params)

## Used in templates (N)
- file:line — `<sel>` snippet

## Programmatic references (N)
- file:line — context

## Tests
- spec_file::test_name (or "no test found")

## Refactor checklist
- [ ] update each template usage if I change a binding
- [ ] update each programmatic reference if I rename
- [ ] update spec(s) if I change the contract
```

Constraints:
- If no audit record returned, the symbol either isn't an
  ng-component (might be `ng-service`, `ng-directive`, etc.) or is
  unknown. Run `find_symbol($ARGUMENTS)` to disambiguate, then suggest
  the right `/ng-*` command.
- Cap each list section at 30 items; show count + top 10 if overflow.
- Don't paste full HTML/TS bodies — link with `file:line`.
- When `selector` is missing, still emit "Used in templates: skipped
  (no static selector — likely a base class or NgModule entry)".
