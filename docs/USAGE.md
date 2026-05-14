# Worked examples

Quick-reference recipes for the `vc-context` CLI.  For
agent-task-shaped sequences (bug investigation, impact analysis,
refactor review) see [`playbooks/`](playbooks/) instead.

> **Setup**: `vc-context build` first to create the `agent_*.json`
> artefacts.  Symlink + MCP wiring instructions live in
> [`README.md`](README.md) and [`MCP_SETUP.md`](MCP_SETUP.md).

---

## Recipe 1 — Find where a symbol lives

```bash
$ vc-context find mark_paid
mark_paid
  file: bot/api_client/orders.py:42-58
  kind: async-func
  params: (order_id: int, payment_id: str)
  role: api-client
  doc: PATCH /api/admin/orders/{id}/paid — returns whether this call …

$ vc-context find mark_paid --json | jq .file
"bot/api_client/orders.py"
```

Collisions resolve shortest-path-wins (canonical definition over a
re-export).  Use `vc-context module <folder>` to disambiguate.

---

## Recipe 2 — Audit every webhook in the project

```bash
$ vc-context role webhook
2 symbol(s) with role 'webhook':
  liqpay_callback
  monopay_callback
```

Two webhooks → two files to open.  Without the role index that's
`grep -r webhook | grep async` plus a manual cross-check.

---

## Recipe 3 — "What does this folder do?"

```bash
$ vc-context module bot/api_client
Module: bot/api_client
  catalog.py
    dependencies: aiohttp, ...
    - create_category [async-func] (api-client)
    - delete_category [async-func] (api-client)
    ...
```

Or for a single file: `vc-context file-card bot/api_client/staff.py`.

---

## Recipe 4 — Reverse lookup: who depends on this file?

Renaming an `api_client` function?  Find every JS + Python caller
without grep:

```bash
$ vc-context route-callers /api/admin/staff/admins
3 JS caller(s) for /api/admin/staff/admins:
  src/app/services/staff.service.ts:18  this.http.get(...)
  src/app/admin/components/staff-list.component.ts:42  ...
  ...

$ vc-context calls add_admin
2 possible caller(s) for add_admin:
  bot/handlers/admin_staff.py  [aiogram-handler]
  bot/handlers/admin/menu.py  [aiogram-handler]
```

---

## Recipe 5 — Check coverage before refactor

```bash
$ vc-context coverage
  api-client       12/12  (100%)
  aiogram-handler  34/41  (83%)
  webhook          0/2    (0%)   ← touch with care
  ...
```

Drill into a specific role:

```bash
$ vc-context --json find_by_role webhook  # or just `role webhook`
```

`coverage_for_role` (MCP) returns the actual list of symbols WITHOUT
tests, so you can fill the gap before changing them.

---

## Recipe 6 — Drive the MCP server by hand

Useful when debugging an MCP host that's silently misbehaving — pipe
JSON-RPC frames straight into stdin:

```bash
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"find_symbol","arguments":{"name":"add_admin"}}}' \
  | python3 .ai-context/mcp_server.py
```

Two responses come back: `initialize` ack and the tool result.  If
this works but your editor doesn't see the tools — the bug's in the
host wiring, not the server.

---

## Recipe 7 — Use it in a text-only LLM (no MCP, no shell)

For an agent that can only read files, the JSON tier still helps:

```
Read these in order, stop when you have an answer:

1. agent_root.json                ← what modules / roles exist
2. agent_symbols.json             ← look up specific symbols
3. <module>/_module_map.json      ← zoom into one folder
4. The actual source file         ← only when editing
```

That ladder is the gist of `AGENT_README.md` (auto-generated).  Point
the agent at it.

---

## Adding new functionality

* New language parser, custom role, MCP tool: see
  [`CONTRIBUTING.md`](CONTRIBUTING.md).
* Convention rules, ignore patterns, http-clients config:
  [`README.md`](README.md) → "Conventions config".

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `vc-context find X` says "not found" but X exists | Index stale | `vc-context build` (or enable the pre-push hook) |
| MCP host: "tool unknown" | Agent loaded before server registered tools | Restart the host. `tools/list` is read once at handshake. |
| `who_calls` empty | Heuristic is import-based; caller uses lazy import | Open the file directly — dynamic imports aren't tracked. Try `find_call_sites` for line-level reverse lookup. |
| Pre-commit "files modified by hook" | Builder updated a map file the index hadn't seen | `git add -A && git commit --amend --no-edit` |
| Empty `agent_symbols.json` after build | Project root has no recognised source files | Check `agent_root.json` — if `modules` is empty, the builder didn't find anything indexable |
| `vc-context: no agent_root.json` | First run in a fresh project | Run `vc-context build` once |
