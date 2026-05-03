# Worked examples

Concrete recipes for the three query surfaces — start here when an
abstract README leaves you cold.

---

## Setup the symlink (once per machine)

```bash
ln -s "$(pwd)/.ai-context/bin/vc-context" /usr/local/bin/vc-context
ln -s "$(pwd)/.ai-context/bin/vc-context-mcp" /usr/local/bin/vc-context-mcp
```

After that `vc-context` and `vc-context-mcp` work from anywhere. The
CLI uses `--root` (default = cwd) to find the project's artifacts.

---

## Recipe 1 — Find where a symbol lives

**You're new to the repo and someone said "look at `mark_paid`".**

```bash
$ vc-context find mark_paid
mark_paid  bot/api_client/orders.py
  async-func (order_id: int, payment_id: str)  [api-client]
  PATCH /api/admin/orders/{id}/paid — returns whether this call …
```

If you wanted JSON to pipe into another tool:

```bash
$ vc-context find mark_paid --json | jq .file
"bot/api_client/orders.py"
```

If the symbol exists in two places, the shortest path wins (canonical
definition, not a re-export). Dig deeper with `vc-context module
<that-folder>` if you suspect a collision.

---

## Recipe 2 — Audit every webhook in the project

**"Are all our payment webhooks idempotent?"** Step one: enumerate
them.

```bash
$ vc-context role webhook
liqpay_callback   backend/routes/webhooks.py
  async-func (request: Request)  [webhook]
  POST /api/liqpay/callback — LiqPay payment webhook (server-to-…
monopay_callback  backend/routes/webhooks.py
  async-func (request: Request)  [webhook]
  POST /api/monopay/callback — Monobank acquiring webhook with …
```

Two webhooks. Now you know exactly which two files to open for the
audit. Without the role index that's `grep -r webhook | grep async`
plus a manual cross-check.

---

## Recipe 3 — "What does this folder do?"

You hopped into a directory and want a one-shot summary before reading
any source:

```bash
$ vc-context module bot/api_client
bot/api_client/  (8 files)

  catalog.py
    create_category, delete_category, refresh_currency,
    add_delivery_provider, toggle_delivery_provider,
    delete_delivery_provider

  staff.py
    add_admin, delete_admin, approve_seller, block_seller,
    unblock_seller

  orders.py
    set_status, set_tracking_shipped, mark_paid, delete,
    refiscalize, answer_pending_question

  …
```

Each line: filename, then the public symbols it exposes. About 2 KB
total — vs ~30 KB if you `cat` every `.py` file in the folder.

---

## Recipe 4 — Reverse lookup: who depends on this file?

```bash
$ vc-context calls add_admin
bot/handlers/admin_staff.py    (imports bot.api_client.staff)
backend/routes/admin_staff.py  (imports services.admin_service via add_admin)
```

Heuristic: walks every `_module_map.json` and reverse-indexes
dependencies. Best-effort — it sees imports, not actual calls — but
catches 90% of real refactor blast-radius.

---

## Recipe 5 — Get a CI guard for "every endpoint has a test"

`vc-context` is shell-friendly — combine subcommands with `comm` /
`jq` for ad-hoc lints:

```bash
# Every `route` symbol that doesn't appear in any test file
vc-context role route --json | jq -r 'keys[]' \
  | sort > /tmp/routes.txt

grep -roh 'def test_[a-z_]*' tests/ \
  | sed 's/def //' | sort -u > /tmp/tests.txt

comm -23 /tmp/routes.txt /tmp/tests.txt
# → routes that have no `test_<routename>` test anywhere
```

Hook this into your CI as a `nightly:` job — auto-flagging routes
that lost test coverage.

---

## Recipe 6 — Driving the MCP server by hand

You don't need an agent to test the MCP layer; pipe JSON-RPC frames
straight into stdin:

```bash
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"find_symbol","arguments":{"name":"add_admin"}}}' \
  | python3 .ai-context/mcp_server.py
```

Two responses come back: `initialize` ack and the tool result. Useful
when debugging an MCP host that's silently misbehaving — you've
verified the server itself works, so the bug is in the host's wiring.

---

## Recipe 7 — Add a custom role for an Express project

Suppose you're using vc-context-builder on a Node/Express backend.
The built-in JS/TS detector tags `app.get(...)`-registered handlers
with `express-route` already, but you also want to flag every
authentication middleware as `auth-middleware` and every Mongoose
model as `model`.

Drop a `.vc-context/roles.json` at the project root:

```json
{
  "roles": [
    {
      "id": "auth-middleware",
      "match_path": "**/middlewares/**/*.{js,ts}",
      "match_function_name": "^(authenticate|requireAuth|isLoggedIn)",
      "priority": 5
    },
    {
      "id": "model",
      "match_path": "**/models/**/*.{js,ts}",
      "match_call": "mongoose\\.model|new\\s+Schema",
      "priority": 5
    },
    {
      "id": "graphql-resolver",
      "match_path": "**/resolvers/**/*.{js,ts}",
      "match_function_name": "Resolver$",
      "priority": 7
    }
  ]
}
```

Then `vc-context build && vc-context role auth-middleware` lists every
auth function in the project. No code changes to the submodule
required — the JSON is read on every build.

Notes:

- Glob supports `**` (any number of segments) and `{a,b}` brace
  alternation.
- Regex matchers are ordinary Python `re` patterns. Test with
  `python3 -c "import re; print(re.search(r'pat', 'sample'))"` first
  if a rule isn't firing.
- Built-in roles have priority 0; bump custom roles above that to
  override (default 5 already does).
- Missing config = no custom roles, no error. Adding a config is
  always opt-in.

---

## Recipe 8 — Add a parser for a new language (Go example)

Say you want Go. Drop one file:

```python
# .ai-context/parsers/go_parser.py
import re
from parsers.base_parser import BaseParser

_FUNC_RE = re.compile(r'^func\s+(?:\(\w+\s+\*?\w+\)\s+)?([A-Z]\w*)\s*\(([^)]*)\)', re.M)
_IMPORT_RE = re.compile(r'^import\s+(?:"([^"]+)"|\(\s*((?:[^)]|\n)+)\))', re.M)


class GoParser(BaseParser):
    extensions = ['.go']

    def extract(self, file_path: str):
        src = self._read_file(file_path)
        if not src:
            return {"exports": [], "dependencies": []}

        exports = [
            {"name": m.group(1), "kind": "func",
             "params": "(" + m.group(2) + ")"}
            for m in _FUNC_RE.finditer(src)
        ]
        deps = set()
        for m in _IMPORT_RE.finditer(src):
            single = m.group(1)
            block  = m.group(2) or ""
            if single:
                deps.add(single.split('/')[-1])
            for line in block.splitlines():
                line = line.strip().strip('"')
                if line:
                    deps.add(line.split('/')[-1])

        return {"exports": exports, "dependencies": sorted(deps)}
```

Add `from parsers.go_parser import GoParser  # noqa` to
`parsers/__init__.py`. Done — `agent_map.py` will pick `.go` files up
on the next run, and `vc-context find <GoSymbol>` works.

---

## Recipe 9 — Use it in a generic LLM (no MCP, no shell)

For an agent that can only read files, the JSON tier still helps:

```
Read these in order, stop when you have an answer:

1. agent_root.json                ← what modules / roles exist
2. agent_symbols.json             ← look up specific symbols
3. <module>/_module_map.json      ← zoom into one folder
4. The actual source file         ← only when editing
```

That ladder is also the gist of `AGENT_README.md`. Refer agents to it.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `vc-context find X` returns "not found" but X exists | Builder hasn't re-run since you wrote it | `vc-context build`, or just `git commit` (pre-commit triggers) |
| MCP host says "tool unknown" | Agent loaded before the server registered tools | Restart the host. `tools/list` is read once at handshake. |
| `who_calls` returns nothing | Heuristic is import-based; the caller doesn't import that file at module level | Open the file directly — dynamic / lazy imports aren't tracked |
| Pre-commit failed: "files modified by hook" | Builder added a new map file the index hadn't seen | `git add -A && git commit --amend --no-edit` |
| Empty `agent_symbols.json` after build | Project root has no recognizable source files | Check `agent_root.json` — if `modules` is empty, the builder didn't see anything indexable |
