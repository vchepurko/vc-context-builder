---
description: One-screen Angular project snapshot — counts per role, standalone components, root-scoped services
argument-hint: (no arguments)
allowed-tools: mcp__vc-context__ng_overview, mcp__vc-context__find_by_role, mcp__vc-context__list_modules
---

Render a high-signal Angular surface map of the current project.
Useful as the very first command on a new clone or when re-orienting
after a long break.

Workflow:
1. Call `ng_overview()`. Returns `{counts: {ng-component, ng-service,
   ng-module, ng-pipe, ng-directive, ng-guard}, standalone_components,
   providers_root: [names...]}`.
2. If `counts.ng-component == 0`, the project isn't an Angular app
   (or the builder hasn't been run yet) — say so and stop.
3. Otherwise, summarise:
   * Counts per role (skip zero rows).
   * Standalone vs NgModule-scoped components ratio.
   * `providersRoot` — list the names; these are the singleton
     services that touch the whole app.
4. **Suggested next steps**: based on which role dominates, recommend a
   follow-up command:
   * Many components → `/ng-audit-component <Name>` to triage one.
   * Many services → `/ng-trace-service <Name>` to map dependencies.
   * Standalone-heavy app → mention NgModules might be migrating away.

Output shape:
```
## Angular surface

**Total: C components, S services, M modules, P pipes, D directives, G guards**

| Role           | Count |
|---|---|
| ng-component   | C |
| ng-service     | S |
| ...            | ... |

**Standalone components:** SC of C  (X% migrated)

**Root-scoped services (`providedIn: 'root'`):**
- ServiceA
- ServiceB
- ...

## Next steps
- Pick a high-traffic component → `/ng-audit-component`
- Pick a root service → `/ng-trace-service`
```

Constraints:
- Don't list every component — just the role counts and root
  services. The full list of components per role is one
  `find_by_role("ng-component")` call away if the user asks.
- If `providers_root` is huge (>20), show top 10 alphabetically and
  total count.
