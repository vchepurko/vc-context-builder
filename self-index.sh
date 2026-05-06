#!/usr/bin/env bash
# Index vc-context-builder against itself.
#
# When the submodule is wired into a parent project, the project's
# ``.mcp.json`` points the vc-context server at the parent root —
# ``find_symbol("Dispatcher")`` finds nothing because the submodule's
# own code isn't indexed.  Run this script to generate
# ``.ai-context/agent_*.json`` artifacts for the SUBMODULE's tree, so
# Claude / Cursor can browse the builder's internals via a second MCP
# entry pointing at the submodule root.
#
# Pair with this snippet inside the parent project's ``.mcp.json``:
#
#     {
#       "mcpServers": {
#         "vc-context": {                                  // existing — parent
#           "command": "python3",
#           "args": [".ai-context/mcp_server.py", "--root", "."],
#           "type": "stdio"
#         },
#         "vc-context-self": {                             // NEW — submodule
#           "command": "python3",
#           "args": [".ai-context/mcp_server.py", "--root", ".ai-context"],
#           "type": "stdio"
#         }
#       }
#     }
#
# Tools then surface as ``mcp__vc_context__*`` (parent project) and
# ``mcp__vc_context_self__*`` (submodule) — pick the prefix that
# matches the codebase you're navigating.
#
# Usage:
#     bash .ai-context/self-index.sh           # one-shot
#     bash .ai-context/self-index.sh --watch   # rerun on every git
#                                              # commit (registers a
#                                              # native pre-commit hook
#                                              # in .githooks/)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCH=false
for arg in "$@"; do
    case "$arg" in
        --watch|-w) WATCH=true ;;
        -h|--help)
            sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown flag: $arg (try --help)" >&2; exit 2 ;;
    esac
done

cd "$SCRIPT_DIR"
echo "📦 Indexing vc-context-builder against itself..."
python3 agent_map.py

if $WATCH; then
    HOOK_DIR="$SCRIPT_DIR/.githooks"
    HOOK_FILE="$HOOK_DIR/pre-commit-self-index"
    mkdir -p "$HOOK_DIR"
    cat > "$HOOK_FILE" <<'HOOK'
#!/usr/bin/env bash
# Auto-installed by self-index.sh --watch — rebuild the submodule's
# own agent_*.json artifacts before each commit so they stay current.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"
python3 agent_map.py >/dev/null 2>&1 || true
HOOK
    chmod +x "$HOOK_FILE"
    echo "✅ pre-commit hook installed at $HOOK_FILE"
    echo "   To activate: git config core.hooksPath .githooks"
fi

echo "✅ Self-index complete.  Add a 'vc-context-self' entry to your"
echo "   parent project's .mcp.json (see this script's --help) to"
echo "   surface the submodule's tools alongside the project ones."
