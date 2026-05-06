---
description: List every call site of a callable with surrounding context, ready for batch refactor
argument-hint: <callable_name> [match_path_glob]
allowed-tools: mcp__vc-context__find_call_sites, mcp__vc-context__who_calls, mcp__vc-context__find_symbol, mcp__vc-context__route_callers, Read
---

Map every place that uses `$ARGUMENTS` so a signature/behaviour change can be applied cleanly.

Argument parsing:
- First whitespace-separated token = callable name.
- Optional second token = path glob to scope the search (e.g. `bot/handlers/**`).
- If empty: stop and ask.

Workflow:
1. `find_symbol(<name>)` to confirm it exists and capture its declared `file:line`.
2. `find_call_sites(callable=<name>, match_path=<glob if given>)` for direct calls.
3. `who_calls(<name>)` to capture the inbound graph (parents, transitive callers).
4. If the callable corresponds to an HTTP route, also run `route_callers` so JS-side callers surface.
5. **Group call sites by**:
   - same module (fastest mass-edit target)
   - same role (handlers vs services vs background)
   - same calling shape (positional vs kwargs — signature changes hit these differently)
6. For each call site, Read 2 lines before + 2 lines after for context (do this in parallel; cap at 25 sites).

Output:
```
## Declared: file:line link

## Direct call sites (N)
- file:line — `<one-line context>`
…

## Indirect callers (transitive)
- file:line — wraps via <fn>

## Refactor batches
1. <module/glob> — N sites — same shape — safe to mass-edit
2. <module/glob> — N sites — heterogeneous — review each
```

End with: a single concrete edit plan (which batch to do first, what regex/AST change to apply, which tests to run after).

Constraints:
- ≤ 25 call sites in detail; collapse the rest as `(+N in <module>)`.
- Markdown links for every reference.
- Skip files inside `.venv`, `node_modules`, `.ai-context/__pycache__`.

## Token cost

| Path | Approx tokens |
|---|---|
| This command (3–4 MCP calls + per-site Read) | ~1–3K depending on N |
| Manual: ripgrep + Read each hit's full file | ~20–60K (every caller's full file enters context) |
| Savings | ~90–95% |

End the report with `_Used 4 MCP calls + N targeted Reads (~M tokens)
— saved ~XK vs ripgrep+full-file Reads._`
