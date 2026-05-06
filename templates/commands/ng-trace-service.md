---
description: Trace an Angular @Injectable service — providedIn scope, every component/service that injects it, downstream method calls
argument-hint: <ServiceClassName>
allowed-tools: mcp__vc-context__find_symbol, mcp__vc-context__inspect_class, mcp__vc-context__ng_inject_graph, mcp__vc-context__find_call_sites, mcp__vc-context__find_test, Read
---

Map the blast radius of Angular service `$ARGUMENTS` before refactoring
its public API.

Workflow:
1. **Skeleton + scope:** call `find_symbol($ARGUMENTS)` to confirm
   `role == "ng-service"` and pick up `ng_provided_in` (`'root'` /
   module name / undefined).
2. **Class shape:** call `inspect_class($ARGUMENTS)` for the public
   methods — those are the API surface that callers depend on.
3. **Injection sites:** call `ng_inject_graph($ARGUMENTS)`. Returns
   `[{file, line, kind}]` where `kind` is `'constructor'` or `'inject'`
   — both the classic DI and Angular 14+ functional `inject()`.
4. **Call sites per method:** for each public method discovered in step
   2, call `find_call_sites(<method>, match_path="**/*.ts")` to see
   *which methods of the service are actually used* and where. Empty
   set = dead method, candidate for removal.
5. **Tests:** call `find_test($ARGUMENTS)`.

Output shape:
```
## $ARGUMENTS — trace

**File:** `<path>`
**Scope:** providedIn=`<root|module|—>`
**Public methods:** N

## Injected by (N)
- file:line — constructor / inject()

## Method usage
- methodA() — used in 7 places: components/foo, services/bar, ...
- methodB() — UNUSED (delete candidate)
- methodC() — 2 places

## Tests
- spec_file::test_name

## Refactor risk
- Renaming `methodA` ⇒ touches 7 files (high)
- Removing `methodB` ⇒ safe (unused)
- Changing return type of `methodC` ⇒ 2 callers to migrate
```

Constraints:
- If `find_symbol` returns role != `ng-service`, suggest the right
  `/ng-*` command and stop.
- Cap method-usage lists to 20 items per method.
- Always run `find_call_sites` *per method*, not per service — service
  references in `import` lines aren't useful, only call sites are.
- For services with 0 injection sites, flag as dead-code candidate.
