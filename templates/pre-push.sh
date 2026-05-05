#!/usr/bin/env bash
# vc-context-builder: native git pre-push hook.
#
# Rebuilds agent_*.json / _module_map.json before every push, OUTSIDE
# the pre-commit framework — so it doesn't race with autofix hooks
# stashing unstaged changes ("Stashed changes conflicted with hook
# auto-fixes" loop). Two modes:
#
#   * local-mode  (.git/vc-context-local present)  — rebuild only,
#     never stage. Personal scratch.
#   * project-mode                                — rebuild and stage
#     the artifacts. If the rebuild produced fresh changes, abort the
#     push so the user can decide explicitly (extra commit / amend /
#     drop). Never silently --amend on the user's behalf.
#
# Enable once per clone via vc-context-builder's installer:
#     .ai-context/install.sh           (auto-detected; uses native hook)
#     .ai-context/install.sh --native  (force native, even if
#                                       pre-commit is present)
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
GIT_DIR="$(git rev-parse --absolute-git-dir)"
LOCAL_MARKER="$GIT_DIR/vc-context-local"

cd "$REPO_ROOT"

BUILDER="$REPO_ROOT/.ai-context/agent_map.py"
if [ ! -f "$BUILDER" ]; then
    echo "⚠️  vc-context: $BUILDER missing — skipping rebuild." >&2
    exit 0
fi

echo "🔁 vc-context: rebuilding agent maps..."
if command -v uv >/dev/null 2>&1; then
    uv run python3 "$BUILDER" >/dev/null
else
    python3 "$BUILDER" >/dev/null
fi

if [ -f "$LOCAL_MARKER" ]; then
    echo "🔒 local-mode: maps are personal, not staging. Push continues."
    exit 0
fi

# project-mode: surface fresh artifacts to the user, never auto-amend.
ARTIFACT_PATTERNS=(
    "_module_map.json"
    "**/_module_map.json"
    "agent_root.json"
    "agent_symbols.json"
    "agent_tests.json"
    "agent_routes.json"
    "agent_callbacks.json"
    "agent_fsm_flows.json"
    "agent_test_categories.json"
    "AGENT_README.md"
)
git add -- "${ARTIFACT_PATTERNS[@]}" 2>/dev/null || true

if ! git diff --cached --quiet -- "${ARTIFACT_PATTERNS[@]}"; then
    cat <<'MSG' >&2
❌ vc-context: agent maps changed during pre-push.
   They are now staged. Decide explicitly:

     git commit -m "regen vc-context maps"   # extra commit
     git commit --amend --no-edit            # or fold into the last
     git reset HEAD -- <files>               # or drop them

   Then re-run `git push`.
MSG
    exit 1
fi

echo "✅ vc-context: maps already up to date."
