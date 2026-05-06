---
description: Find structurally similar code (handlers, callbacks, symbols, role members) via vc-context MCP — for uniform refactoring across the bot
argument-hint: <symbol_or_pattern>
allowed-tools: mcp__vc-context__find_symbol, mcp__vc-context__find_callback, mcp__vc-context__find_call_sites, mcp__vc-context__find_by_role, mcp__vc-context__list_roles, mcp__vc-context__who_calls, mcp__vc-context__route_callers, mcp__vc-context__trace_fsm_flow, Read, Bash
---

Find code similar to `$ARGUMENTS` so a refactor can be applied uniformly across the codebase.

Treat the argument as one of:
- a **symbol name** (function/class) → `find_symbol`, then `find_call_sites` and `who_calls` for neighbours
- a **callback_data** (e.g. `seller_approve_*`) → `find_callback`, then collect all callbacks sharing the same prefix or aiogram filter shape
- a **role** (e.g. `seller`, `client`, `admin`) → `list_roles`, `find_by_role`, group similar handlers by FSM state / callback shape
- an **FSM state name** → `trace_fsm_flow`, then list other states that follow the same lifecycle pattern (entry handler → text handler → confirm callback)
- a **route path** → `route_callers`

Workflow:
1. Classify the argument by shape (state name CamelCase + `.`, callback prefix `cb_*`, role lowercase, plain symbol).
2. Run the matching MCP lookup. Do NOT grep first — vc-context is authoritative.
3. From the primary hit, fan out: callers, sibling handlers in the same router file, handlers with the same decorator shape.
4. **Group results by similarity dimension**: same decorator pattern, same state filter, same locale namespace, same repository call, same router module.
5. For each group, list `file:line` references using markdown links so the user can click through.
6. **End with a ranked list of refactor candidates** — which group is most likely to need the same change, with a one-line justification per group.

Constraints:
- Cap output around 30 results per group; if a group is larger, summarise count + show top 10.
- Never paste full function bodies — link with `file:line` only, unless the user asks for snippets.
- If no matches, say so and propose 1-2 alternative searches (different role, different prefix, etc.).
- If `$ARGUMENTS` is empty, ask which dimension to search and stop.

Output shape:
```
## Primary: <what was matched>
- file:line — short label

## Similar by <dimension A>
…

## Similar by <dimension B>
…

## Refactor candidates (ranked)
1. <group> — <why>
```

## Token cost

| Path | Approx tokens |
|---|---|
| This command (3–5 MCP calls) | ~400–700 |
| Manual: grep + Read 10–20 candidate files for context | ~15–40K |
| Savings | ~95–98% |

Append `_Used N MCP calls (~M tokens) — saved ~XK vs grep+read._` at
the bottom so the user can see the win.
