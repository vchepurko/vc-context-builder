# Wiring `vc-context` into your MCP host

The submodule ships an stdio JSON-RPC server (`mcp_server.py`) and a
shell wrapper (`bin/vc-context-mcp`). Point your MCP-aware client at
that wrapper and it will expose six tools:

- `find_symbol(name)` — symbol record (file/kind/params/doc/role)
- `find_by_role(role)` — every symbol with that role tag
- `who_calls(symbol)` — best-effort callers (heuristic, not a true call graph)
- `summarise_module(folder)` — slim per-folder map
- `list_roles()` — `role -> count`
- `list_modules()` — scanned folders

> All paths below assume the parent project lives at
> `~/projects/klodchickknifes`. Adjust if yours is different.

---

## Claude Code

Add to `~/.claude/mcp.json` (create the file if needed):

```json
{
  "mcpServers": {
    "vc-context": {
      "command": "/Users/you/projects/klodchickknifes/.ai-context/bin/vc-context-mcp",
      "args": [],
      "env": {},
      "cwd": "/Users/you/projects/klodchickknifes"
    }
  }
}
```

`cwd` matters — the server reads `agent_root.json` and
`agent_symbols.json` from there.

You can also register it with the CLI:

```bash
claude mcp add vc-context \
  /Users/you/projects/klodchickknifes/.ai-context/bin/vc-context-mcp \
  --cwd /Users/you/projects/klodchickknifes
```

---

## Cursor

Add to `.cursor/mcp.json` at the **project root** (so the working
directory is correct):

```json
{
  "mcpServers": {
    "vc-context": {
      "command": ".ai-context/bin/vc-context-mcp",
      "args": []
    }
  }
}
```

Restart Cursor; the tools show up under the MCP picker.

---

## Codex CLI

Codex reads `~/.config/codex/config.toml`:

```toml
[mcp_servers.vc-context]
command = "/Users/you/projects/klodchickknifes/.ai-context/bin/vc-context-mcp"
args = []
```

For per-project scoping, drop the same block into the project's
`AGENTS.md`-adjacent config.

---

## Continue.dev

`~/.continue/config.json`:

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type": "stdio",
          "command": "/Users/you/projects/klodchickknifes/.ai-context/bin/vc-context-mcp"
        }
      }
    ]
  }
}
```

---

## Generic stdio MCP host

Any host that speaks MCP over stdio works the same way:

- **Command**: `python3 .ai-context/mcp_server.py`
  (or the wrapper: `.ai-context/bin/vc-context-mcp`)
- **Working directory**: the project root that contains
  `agent_root.json` (the server defaults to `cwd`; pass
  `--root /abs/path` if you must).
- **Transport**: stdio, line-delimited JSON-RPC 2.0.

---

## Smoke test

You can drive the server by hand to confirm it's wired up:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python3 .ai-context/mcp_server.py | head -1
```

You should see a JSON response with `serverInfo.name = "vc-context"`.

```bash
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_roles","arguments":{}}}' \
  | python3 .ai-context/mcp_server.py
```

The second response carries the `role -> count` map as text content.

---

## When the server reports `missing_artifact`

The JSON files have not been built yet (or you launched from the
wrong directory). Run:

```bash
python3 .ai-context/agent_map.py
```

at the project root, or invoke the CLI's `build` subcommand:

```bash
.ai-context/bin/vc-context build
```
