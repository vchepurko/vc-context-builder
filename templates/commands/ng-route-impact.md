---
description: Map an Angular URL path or component to its routes — guards, lazy children, redirect targets, and related components
argument-hint: <path-or-ComponentName>
allowed-tools: mcp__vc-context__ng_route_for_path, mcp__vc-context__ng_routes_for_component, mcp__vc-context__ng_list_routes, mcp__vc-context__find_symbol, mcp__vc-context__ng_audit_component, Read
---

Resolve `$ARGUMENTS` against the project's Angular RouterModule
configuration. Surfaces the route record(s), guards, lazy-loaded
children, and (when known) the component class so the user can answer
"who handles /admin/users?" or "where is HomeComponent mounted?" in
one round-trip.

Argument detection:
- Looks like a class name (capitalised, no slashes) → treat as
  component, call `ng_routes_for_component`.
- Anything else → treat as URL path, call `ng_route_for_path`.

Workflow:
1. Disambiguate: if the argument matches `^[A-Z][A-Za-z0-9_$]*Component`,
   route it through `ng_routes_for_component`. Otherwise normalise
   the path (strip leading `/`, no trailing slash) and call
   `ng_route_for_path`.
2. **Show every match**: include `file:line`, lazy flag, guards,
   redirect target. If the route is lazy (`loadChildren`),
   note that the children aren't expanded — point at the file so
   the user can read the deferred module.
3. **Component context**: for each route with a non-null `component`,
   call `find_symbol(<Component>)` so the user gets the file path,
   then optionally suggest `/ng-audit-component <Component>` for a
   deeper read.
4. **Guards**: each guard listed in the route record is itself a
   resolvable symbol — surface guard files via `find_symbol(<Guard>)`
   so the user can see what auth/feature flags gate the route.

Output shape:
```
## Routes matching `<path>` (N)

### `<path>` → `<Component>` at file:line
- Lazy:     yes/no
- Guards:   AuthGuard at file:line, RoleGuard at file:line
- Redirect: <target>  (or "—")

### Component file
`src/app/.../my.component.ts`  → `/ng-audit-component MyComponent` for full audit
```

Constraints:
- Empty result → suggest `ng_list_routes()` to dump the surface and
  spot the closest match (typo? wrong segment?).
- Substring matches show in a separate "## Possible matches" section
  with the original path that triggered them, so the user knows the
  hit isn't exact.
- Never paste route-array source — link with `file:line` only.

## Token cost

| Path | Approx tokens |
|---|---|
| This command (2–4 MCP calls) | ~300–600 |
| Manual: Read every *-routing.module.ts + grep guards + Read components | ~10–20K |
| Savings | ~93–97% |

End with `_Used N MCP calls (~M tokens) — saved ~XK vs reading routing
modules + chasing guards._`
