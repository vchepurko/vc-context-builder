#!/usr/bin/env bash
# Re-sync curated slash commands from the submodule into the parent
# project's .claude/commands/.
#
# Use this after `git submodule update --remote .ai-context` brings in
# new commands — install.sh will only copy NEW files (existing ones
# are skipped to protect local edits), but you might want everything
# rebuilt from upstream. By default this only adds missing files, same
# as install.sh; pass --force to overwrite locals.
#
# Usage:
#     bash .ai-context/sync-commands.sh           # add missing only
#     bash .ai-context/sync-commands.sh --force   # also overwrite existing
set -euo pipefail

FORCE=false
for arg in "$@"; do
    case "$arg" in
        --force|-f) FORCE=true ;;
        -h|--help)
            sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown flag: $arg (try --help)" >&2; exit 2 ;;
    esac
done

# Resolve the submodule root regardless of where the user invoked us
# from (parent project, or already inside .ai-context for some reason).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/templates/commands"
DST="$(pwd)/.claude/commands"

if [ ! -d "$SRC" ]; then
    echo "❌ source dir not found: $SRC" >&2
    exit 1
fi

mkdir -p "$DST"
ADDED=0
SKIPPED=0
OVERWROTE=0
for src in "$SRC"/*.md; do
    [ -f "$src" ] || continue
    name=$(basename "$src")
    dst="$DST/$name"
    if [ -f "$dst" ]; then
        if $FORCE; then
            cp "$src" "$dst"
            OVERWROTE=$((OVERWROTE + 1))
        else
            SKIPPED=$((SKIPPED + 1))
        fi
    else
        cp "$src" "$dst"
        ADDED=$((ADDED + 1))
    fi
done

echo "✅ slash commands sync"
echo "   added:      $ADDED"
echo "   overwrote:  $OVERWROTE"
echo "   skipped:    $SKIPPED  (use --force to overwrite)"
