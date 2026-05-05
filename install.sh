#!/usr/bin/env bash
# vc-context-builder installer.
#
# Detects whether the parent project already uses pre-commit
# (https://pre-commit.com): if so, registers vc-context-builder as a
# local hook entry in `.pre-commit-config.yaml` and re-installs the
# framework hook. Otherwise falls back to writing a standalone
# `.git/hooks/pre-commit` (preserving any existing one as
# `.legacy.<ts>`).
#
# Idempotent: re-running won't duplicate hook entries.
#
# Flags:
#   --local-only   Personal mode for THIS clone only — the hook
#                  rebuilds artifacts but never stages them, and
#                  the artifacts are added to .git/info/exclude so
#                  they don't clutter `git status`. Toggleable
#                  per-developer; not project-wide.
#   --no-local     Disable personal mode — go back to project-wide
#                  staging. Removes the marker + cleans up exclude.
#   --native       Install a NATIVE git pre-push hook (under
#                  ./.githooks, enabled via `git config
#                  core.hooksPath`). Skips the pre-commit framework
#                  integration. Recommended for new projects: avoids
#                  the "Stashed changes conflicted with hook
#                  auto-fixes" race that happens when vc-context
#                  rewrites files inside pre-commit's stash window.
#   -h, --help     Show this help.
set -e

LOCAL_ONLY=false
NO_LOCAL=false
NATIVE_HOOK=false
for arg in "$@"; do
    case "$arg" in
        --local-only) LOCAL_ONLY=true ;;
        --no-local)   NO_LOCAL=true ;;
        --native)     NATIVE_HOOK=true ;;
        -h|--help)
            sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown flag: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done

if $LOCAL_ONLY && $NO_LOCAL; then
    echo "❌ --local-only and --no-local are mutually exclusive." >&2
    exit 2
fi

BUILDER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$BUILDER_DIR")"
RELATIVE_DIR=$(basename "$BUILDER_DIR")

cd "$PROJECT_ROOT" || exit 1

echo "🤖 Installing vc-context-builder into parent project..."

# 1. Initial context build (always — needed for every install).
python3 "$RELATIVE_DIR/agent_map.py"

# 2. Decide hook strategy.
#
# The actual work runs from a single helper script
# (`bin/vc-context-build-hook`) so the hook entry stays one line and
# local-mode handling lives in one place.
HOOK_ENTRY="$RELATIVE_DIR/bin/vc-context-build-hook"
PRECOMMIT_CONFIG=".pre-commit-config.yaml"

if $NATIVE_HOOK; then
    echo "📌 --native: installing as a native git pre-push hook (skipping pre-commit framework)."
    mkdir -p .githooks
    cp "$BUILDER_DIR/templates/pre-push.sh" .githooks/pre-push
    chmod +x .githooks/pre-push
    git config core.hooksPath .githooks
    echo "🔧 Hook copied to .githooks/pre-push and core.hooksPath set."
elif [ -f "$PRECOMMIT_CONFIG" ]; then
    echo "📌 Detected $PRECOMMIT_CONFIG — integrating as a pre-commit framework hook."

    if grep -q "vc-context-builder" "$PRECOMMIT_CONFIG"; then
        echo "ℹ️  Hook entry already present in $PRECOMMIT_CONFIG — skipping append."
    else
        cat <<HOOK >> "$PRECOMMIT_CONFIG"

  - repo: local
    hooks:
      - id: vc-context-builder
        name: vc-context-builder (agent context maps)
        entry: $HOOK_ENTRY
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
exec "\$(git rev-parse --show-toplevel)/$HOOK_ENTRY"
HOOK
    chmod +x .git/hooks/pre-commit
fi

# 3. Local-mode toggle.
GIT_DIR="$(git rev-parse --absolute-git-dir 2>/dev/null || true)"
MARKER="$GIT_DIR/vc-context-local"
EXCLUDE="$GIT_DIR/info/exclude"
EXCLUDE_BEGIN="# >>> vc-context-builder local mode"
EXCLUDE_END="# <<< vc-context-builder local mode"

strip_exclude_block() {
    local f="$1"
    [ -f "$f" ] || return 0
    grep -q "$EXCLUDE_BEGIN" "$f" || return 0
    # `END` is a reserved pattern in awk — use ENDM / BEGM to avoid it.
    awk -v BEGM="$EXCLUDE_BEGIN" -v ENDM="$EXCLUDE_END" '
        $0 == BEGM { skip=1; next }
        $0 == ENDM { skip=0; next }
        !skip { print }
    ' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
}

if $LOCAL_ONLY; then
    if [ -z "$GIT_DIR" ]; then
        echo "❌ --local-only requires a git directory; not in a git repo?" >&2
        exit 1
    fi
    touch "$MARKER"
    mkdir -p "$(dirname "$EXCLUDE")"
    strip_exclude_block "$EXCLUDE"  # idempotent
    {
        echo "$EXCLUDE_BEGIN"
        echo "agent_root.json"
        echo "agent_symbols.json"
        echo "agent_tests.json"
        echo "agent_routes.json"
        echo "AGENT_README.md"
        echo "**/_module_map.json"
        echo "$EXCLUDE_END"
    } >> "$EXCLUDE"

    echo ""
    echo "🔒 Local mode enabled — artifacts will be regenerated but never staged."
    echo "    Marker:  $MARKER"
    echo "    Exclude: $EXCLUDE"

    TRACKED=$(git ls-files \
        agent_root.json \
        agent_symbols.json \
        agent_tests.json \
        agent_routes.json \
        AGENT_README.md \
        '*_module_map.json' \
        '**/*_module_map.json' \
        2>/dev/null || true)
    if [ -n "$TRACKED" ]; then
        echo ""
        echo "⚠️  These artifact files are already tracked in this repo:"
        echo "$TRACKED" | sed 's/^/      /'
        echo ""
        echo "    Local mode prevents future staging, but won't untrack them."
        echo "    To untrack:  git rm --cached <files>  (then commit)."
    fi
elif $NO_LOCAL; then
    if [ -n "$GIT_DIR" ] && [ -f "$MARKER" ]; then
        rm "$MARKER"
        echo "🔓 Local mode disabled."
    else
        echo "ℹ️  Local mode was not active."
    fi
    if [ -f "$EXCLUDE" ] && grep -q "$EXCLUDE_BEGIN" "$EXCLUDE"; then
        strip_exclude_block "$EXCLUDE"
        echo "    Cleaned up $EXCLUDE."
    fi
elif [ -n "$GIT_DIR" ] && [ -f "$MARKER" ]; then
    echo ""
    echo "🔒 Local mode is currently ENABLED for this clone."
    echo "    Pre-commit will rebuild but NOT stage artifacts."
    echo "    Disable with: ./$RELATIVE_DIR/install.sh --no-local"
fi

# 4. NOTE: the previous version blindly appended `$RELATIVE_DIR/` to the
#    parent .gitignore. Removed: when the builder lives as a git
#    submodule that breaks tracking; when it's vendored, project owners
#    decide themselves whether to ignore.

echo ""
echo "🎉 Installation complete!"
echo "    Builder runs automatically on every commit via pre-commit."
echo "    Manual run:    python3 $RELATIVE_DIR/agent_map.py"
echo "    Local mode:    ./$RELATIVE_DIR/install.sh --local-only"
echo "    Project mode:  ./$RELATIVE_DIR/install.sh --no-local"
