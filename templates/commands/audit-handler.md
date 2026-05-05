---
description: Full pre-refactor audit of an aiogram handler — state filter, callbacks, locale keys, tests, callers, lint risk
argument-hint: <handler_function_name>
allowed-tools: mcp__vc-context__find_symbol, mcp__vc-context__find_test, mcp__vc-context__find_call_sites, mcp__vc-context__who_calls, mcp__vc-context__find_callback, mcp__vc-context__trace_fsm_flow, mcp__vc-context__find_locale_key, mcp__vc-context__lint_violations, mcp__vc-context__coverage_for_role, Read
---

Build a refactor-ready audit of handler `$ARGUMENTS` so the user can change it safely.

If `$ARGUMENTS` is empty: stop and ask which handler.

Steps (fan out in parallel where independent):
1. `find_symbol($ARGUMENTS)` → resolve `file:line` and the role.
2. Read the handler body and its router file's imports + neighbouring handlers.
3. **Decorator audit**: capture every `@router.<event>(...)` line above it. Flag the
   `@router.message(F.text)` without state filter pitfall (see CLAUDE.md aiogram pitfalls).
4. **State lifecycle**: if the decorator references an FSM state, run `trace_fsm_flow` for that state — list entry, transitions, exits.
5. **Callbacks used**: parse `callback_data=` strings inside the handler body, run `find_callback` on each to map sibling handlers.
6. **Locale keys**: extract every `t("key", lang, …)` and `_("key")` call; for each run `find_locale_key` to confirm both `uk` and `en` have it. Flag missing translations.
7. **Tests**: `find_test($ARGUMENTS)` for the direct test; `coverage_for_role(role)` for the role-level gap.
8. **Callers**: `who_calls($ARGUMENTS)` and `find_call_sites($ARGUMENTS)` so we know who breaks if signature changes.
9. **Lint risk**: `lint_violations()` filtered to the handler's file — note any rule that applies to its surface.

Output a single markdown report with these sections:
- **Location**: `file:line` link
- **Decorators**: list with risk flags
- **State lifecycle**: bullet list (skip if non-FSM)
- **Callbacks**: `cb_data` → handler `file:line`
- **Locale keys**: key, namespace, uk/en presence
- **Tests**: hit + gap (from coverage_for_role)
- **Callers**: file:line list
- **Lint**: matching violations or "clean"
- **Refactor checklist**: ordered list of what must change in lockstep

Constraints:
- Each section ≤ 15 lines; if longer, collapse with a `(+N more)` marker.
- Use markdown links for every file reference: `[file.py:42](path/file.py#L42)`.
- Never copy the full handler body unless the user asks.
