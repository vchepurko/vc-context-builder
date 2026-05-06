---
description: Find every HTML template that uses an Angular selector — element form, attribute-directive form, or both
argument-hint: <selector-name> [match_path-glob]
allowed-tools: mcp__vc-context__ng_uses_selector, mcp__vc-context__find_in_templates, mcp__vc-context__find_symbol, Read
---

Locate where Angular selector `$ARGUMENTS` is consumed in HTML
templates. Useful for: pre-rename audits, dead-component detection,
finding the components that need to migrate when you change a binding
contract.

Argument parsing:
- One word → selector, scope = whole project.
- Two words → first is selector, second is `match_path` glob (e.g.
  `app-cart-item src/app/modules/cart/**`).

Workflow:
1. **Resolve to component (optional):** if the selector follows
   `app-foo` / `mat-button` / `pp-toggle` shape, try `find_symbol(<Cap
   case form>)` to surface the owning component class. Show its file
   path so the user can jump to the source.
2. **Template hits:** call `ng_uses_selector(selector, match_path?)`.
   This wraps `find_in_templates` with two passes — `<selector` and
   `[selector]` — deduped by `(file, line)`.
3. **Group by directory:** in the output, group results by their
   immediate parent directory. Templates that live next to each other
   usually move together in a refactor.
4. **Total + dead-code signal:** if the result list is empty, flag
   "selector unused in templates — either programmatic-only, dead, or
   misspelled". Suggest re-running with the alternative form
   (kebab-case ↔ camelCase).

Output shape:
```
## Selector `<selector>`

**Component class:** `<ClassName>` at `<file>` (when resolved)

## Used in N templates

### src/app/modules/foo/
- foo.component.html:42 — `<sel [input]="x">`
- foo.component.html:71 — `<sel>` (closing tag elision)

### src/app/modules/bar/
- bar.component.html:18 — `[sel]="cond"` (attribute directive form)

## Quick stats
- Element form `<sel>`: M occurrences
- Attribute form `[sel]`: K occurrences
- Distinct files: F
```

Constraints:
- Cap output at 100 hits (the underlying tool already truncates).
- Empty match_path = whole project; pass it through verbatim.
- Don't paste more than 80 chars per snippet line — truncate with `…`.
- If both element and attribute forms appear, mention both — sometimes
  the same name does double duty (e.g. `<my-tooltip>` element + `[my-
  tooltip]` directive on a host element).

## Token cost

| Path | Approx tokens |
|---|---|
| This command (1–2 MCP calls) | ~100–300 |
| Manual: ripgrep across HTML, paste 5–15 hit snippets into context | ~3–10K |
| Savings | ~95–97% |

End with `_Used N MCP calls (~M tokens) — saved ~XK vs ripgrep+paste._`
