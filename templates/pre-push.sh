#!/usr/bin/env bash
# vc-context-builder: native git pre-push hook.
#
# Rebuilds agent_*.json / _module_map.json in the BACKGROUND after every
# push so the MCP index stays fresh without blocking `git push`.
#
# Enable once per clone:
#     git config core.hooksPath .githooks
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

BUILDER="$REPO_ROOT/.ai-context/agent_map.py"
if [ ! -f "$BUILDER" ]; then
    exit 0
fi

# Run rebuild in the background — push completes immediately.
if command -v uv >/dev/null 2>&1; then
    nohup uv run python3 "$BUILDER" >/dev/null 2>&1 &
else
    nohup python3 "$BUILDER" >/dev/null 2>&1 &
fi

exit 0
