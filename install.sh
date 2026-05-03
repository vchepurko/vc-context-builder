#!/usr/bin/env bash
# vc-context-builder installer.
#
# Detects whether the parent project already uses pre-commit
# (https://pre-commit.com): if so, registers vc-context-builder as a
# local hook entry in `.pre-commit-config.yaml` and re-installs the
# framework hook. Otherwise falls back to writing a standalone
# `.git/hooks/pre-commit` (preserving any existing one as `.legacy.<ts>`).
#
# Idempotent: re-running won't duplicate hook entries.

set -e

BUILDER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$BUILDER_DIR")"
RELATIVE_DIR=$(basename "$BUILDER_DIR")

cd "$PROJECT_ROOT" || exit 1

echo "🤖 Installing vc-context-builder into parent project..."

# 1. Initial context build (always — needed for every install).
python3 "$RELATIVE_DIR/agent_map.py"

# 2. Decide hook strategy.
PRECOMMIT_CONFIG=".pre-commit-config.yaml"

if [ -f "$PRECOMMIT_CONFIG" ]; then
    echo "📌 Detected $PRECOMMIT_CONFIG — integrating as a pre-commit framework hook."

    if grep -q "vc-context-builder" "$PRECOMMIT_CONFIG"; then
        echo "ℹ️  Hook entry already present in $PRECOMMIT_CONFIG — skipping append."
    else
        cat <<HOOK >> "$PRECOMMIT_CONFIG"

  - repo: local
    hooks:
      - id: vc-context-builder
        name: vc-context-builder (agent context maps)
        entry: bash -c 'python3 $RELATIVE_DIR/agent_map.py && git add -- "**/*_module_map.json" agent_root.json AGENT_README.md 2>/dev/null || true'
        language: system
        pass_filenames: false
        always_run: true
HOOK
        echo "➕ Added vc-context-builder hook to $PRECOMMIT_CONFIG."
    fi

    if command -v pre-commit >/dev/null 2>&1; then
        pre-commit install >/dev/null
        echo "🔧 pre-commit framework re-installed."
    elif command -v uv >/dev/null 2>&1 && uv run pre-commit --version >/dev/null 2>&1; then
        uv run pre-commit install >/dev/null
        echo "🔧 pre-commit (via uv) re-installed."
    else
        echo "⚠️  pre-commit binary not found. Install it (e.g. \`pip install pre-commit\`)"
        echo "    then run: pre-commit install"
    fi
else
    echo "📌 No $PRECOMMIT_CONFIG found — using a standalone pre-commit hook."
    mkdir -p .git/hooks

    # Preserve any existing hook so we don't silently displace ruff/pytest etc.
    if [ -f .git/hooks/pre-commit ] && ! grep -q "vc-context-builder" .git/hooks/pre-commit; then
        TS="$(date +%Y%m%d-%H%M%S)"
        mv .git/hooks/pre-commit ".git/hooks/pre-commit.legacy.$TS"
        echo "💾 Existing hook saved as pre-commit.legacy.$TS — review and merge if needed."
    fi

    cat <<HOOK > .git/hooks/pre-commit
#!/usr/bin/env bash
echo "🤖 vc-context-builder: Updating Agent Context Graph..."
python3 $RELATIVE_DIR/agent_map.py
git add -- "**/*_module_map.json" agent_root.json AGENT_README.md 2>/dev/null || true
echo "✅ Context Graph updated and staged!"
HOOK
    chmod +x .git/hooks/pre-commit
fi

# 3. NOTE: the previous version blindly appended `$RELATIVE_DIR/` to the
#    parent .gitignore. Removed: when the builder lives as a git submodule
#    that breaks tracking; when it's vendored, project owners decide
#    themselves whether to ignore.

echo "🎉 Installation complete!"
echo "    Builder runs automatically on every commit via pre-commit."
echo "    Manual run: python3 $RELATIVE_DIR/agent_map.py"
