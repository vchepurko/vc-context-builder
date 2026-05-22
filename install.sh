#!/usr/bin/env bash
# vc-context-builder installer.
#
# DEFAULT BEHAVIOUR (zero flags, the path you almost always want):
#   1. Build the agent_*.json artifacts.
#   2. Install a NATIVE git pre-push hook (./.githooks/pre-push)
#      that rebuilds artifacts before every push.
#   3. Enable LOCAL MODE — artifacts are rebuilt but never staged,
#      and they're added to .git/info/exclude so they don't clutter
#      `git status`.
#   4. Write a project-rooted .mcp.json so Claude Code (and any other
#      MCP client that reads .mcp.json) auto-discovers vc-context
#      (main project) and vc-context-meta (submodule self-index)
#      with relative paths. Idempotent: skipped if both are already wired.
#   5. Drop curated slash commands (find-similar, audit-handler,
#      refactor-callsites) into ./.claude/commands/. Skipped if files
#      already exist (use --force-commands to overwrite).
#
# Result: one command, MCP works in this clone immediately, agent
# artifacts are invisible from git's perspective, no team-wide
# behaviour change.
#
# Flags (override the defaults — rarely needed):
#   --shared           Project mode: stage rebuilt artifacts and
#                      commit them. Use only if your team agreed to
#                      track the artifacts in git history.
#   --no-mcp           Skip writing .mcp.json (e.g. you manage MCP
#                      registration globally via ~/.claude/mcp.json).
#   --no-commands      Skip copying slash commands.
#   --force-commands   Overwrite existing slash command files (loses
#                      any local edits in .claude/commands/).
#   --auto-reindex[=N] Enable MCP-startup auto-reindex when artifacts
#                      are older than N minutes (default: 60).
#   --auto-reindex-minutes N
#                      Same as above, easier for shells/scripts.
#   --pre-commit       Use the pre-commit framework instead of the
#                      native pre-push hook (legacy path; can race
#                      with autofix hooks during stash/unstash).
#   -h, --help         Show this help.
#
# Legacy flags (kept as no-ops for muscle memory):
#   --local-only / --no-local / --native — pre-1.0 names. Behave as
#   below; --no-local maps to --shared.
set -e

SHARED=false
NO_MCP=false
NO_COMMANDS=false
FORCE_COMMANDS=false
USE_PRECOMMIT=false
AUTO_REINDEX=false
AUTO_REINDEX_MINUTES=60
EXPECT_AUTO_REINDEX_MINUTES=false
for arg in "$@"; do
    if $EXPECT_AUTO_REINDEX_MINUTES; then
        AUTO_REINDEX=true
        AUTO_REINDEX_MINUTES="$arg"
        EXPECT_AUTO_REINDEX_MINUTES=false
        continue
    fi
    case "$arg" in
        --shared)          SHARED=true ;;
        --no-mcp)          NO_MCP=true ;;
        --no-commands)     NO_COMMANDS=true ;;
        --force-commands)  FORCE_COMMANDS=true ;;
        --auto-reindex)    AUTO_REINDEX=true ;;
        --auto-reindex=*)  AUTO_REINDEX=true; AUTO_REINDEX_MINUTES="${arg#*=}" ;;
        --auto-reindex-minutes) EXPECT_AUTO_REINDEX_MINUTES=true ;;
        --pre-commit)      USE_PRECOMMIT=true ;;
        # Legacy aliases.
        --local-only)      ;;  # already default — no-op
        --native)          ;;  # already default — no-op
        --no-local)        SHARED=true ;;  # legacy → --shared
        -h|--help)
            sed -n '2,43p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown flag: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done
if $EXPECT_AUTO_REINDEX_MINUTES; then
    echo "Missing value after --auto-reindex-minutes" >&2
    exit 2
fi
case "$AUTO_REINDEX_MINUTES" in
    ''|*[!0-9]*)
        echo "--auto-reindex minutes must be a positive integer" >&2
        exit 2
        ;;
esac
if [ "$AUTO_REINDEX_MINUTES" -lt 1 ]; then
    echo "--auto-reindex minutes must be >= 1" >&2
    exit 2
fi

BUILDER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$BUILDER_DIR")"
RELATIVE_DIR=$(basename "$BUILDER_DIR")

cd "$PROJECT_ROOT" || exit 1

echo "🤖 vc-context-builder install — $PROJECT_ROOT"

# 1. Initial context build.
python3 "$RELATIVE_DIR/agent_map.py"

# 2. Hook: native pre-push (default) or pre-commit framework (--pre-commit).
HOOK_ENTRY="$RELATIVE_DIR/bin/vc-context-build-hook"
PRECOMMIT_CONFIG=".pre-commit-config.yaml"

if $USE_PRECOMMIT && [ -f "$PRECOMMIT_CONFIG" ]; then
    echo "📌 --pre-commit: integrating with the pre-commit framework."
    if grep -q "vc-context-builder" "$PRECOMMIT_CONFIG"; then
        echo "ℹ️  Hook already present in $PRECOMMIT_CONFIG."
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
        echo "➕ Added hook to $PRECOMMIT_CONFIG."
    fi
    if command -v pre-commit >/dev/null 2>&1; then
        pre-commit install >/dev/null
    elif command -v uv >/dev/null 2>&1 && uv run pre-commit --version >/dev/null 2>&1; then
        uv run pre-commit install >/dev/null
    else
        echo "⚠️  pre-commit binary not found. Install + run: pre-commit install"
    fi
else
    echo "📌 Installing native git pre-push hook (.githooks/pre-push)."
    mkdir -p .githooks
    cp "$BUILDER_DIR/templates/pre-push.sh" .githooks/pre-push
    chmod +x .githooks/pre-push
    git config core.hooksPath .githooks
fi

# 3. Mode toggle (local by default, --shared opts out).
GIT_DIR="$(git rev-parse --absolute-git-dir 2>/dev/null || true)"
MARKER="$GIT_DIR/vc-context-local"
EXCLUDE="$GIT_DIR/info/exclude"
EXCLUDE_BEGIN="# >>> vc-context-builder local mode"
EXCLUDE_END="# <<< vc-context-builder local mode"

strip_exclude_block() {
    local f="$1"
    [ -f "$f" ] || return 0
    grep -q "$EXCLUDE_BEGIN" "$f" || return 0
    awk -v BEGM="$EXCLUDE_BEGIN" -v ENDM="$EXCLUDE_END" '
        $0 == BEGM { skip=1; next }
        $0 == ENDM { skip=0; next }
        !skip { print }
    ' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
}

if $SHARED; then
    if [ -n "$GIT_DIR" ] && [ -f "$MARKER" ]; then
        rm "$MARKER"
        echo "🔓 Shared mode — artifacts will be staged on push."
    fi
    [ -f "$EXCLUDE" ] && strip_exclude_block "$EXCLUDE"
else
    if [ -z "$GIT_DIR" ]; then
        echo "❌ Default (local) mode requires a git directory. Run \`git init\` first or pass --shared after committing." >&2
        exit 1
    fi
    touch "$MARKER"
    mkdir -p "$(dirname "$EXCLUDE")"
    strip_exclude_block "$EXCLUDE"
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
    echo "🔒 Local mode — artifacts hidden from git status."

    TRACKED=$(git ls-files \
        agent_root.json agent_symbols.json agent_tests.json agent_routes.json \
        AGENT_README.md '*_module_map.json' '**/*_module_map.json' \
        2>/dev/null || true)
    if [ -n "$TRACKED" ]; then
        echo "⚠️  Already-tracked artifacts (won't be untracked automatically):"
        echo "$TRACKED" | sed 's/^/      /'
        echo "      Untrack with: git rm --cached <files> (then commit)."
    fi
fi

# 4. Project-rooted MCP config (.mcp.json) — auto-wires Claude Code.
if ! $NO_MCP; then
    MCP_CONFIG=".mcp.json"
    STATUS=$(python3 - "$MCP_CONFIG" "$RELATIVE_DIR" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
relative_dir = sys.argv[2]
entry = {
    "type": "stdio",
    "command": f"{relative_dir}/bin/vc-context-mcp",
    "args": [],
    "env": {},
}
meta_entry = {
    "type": "stdio",
    "command": f"{relative_dir}/bin/vc-context-mcp",
    "args": ["--root", relative_dir],
    "env": {},
}
if path.exists():
    try:
        cfg = json.loads(path.read_text())
    except json.JSONDecodeError:
        print("invalid")
        sys.exit(0)
    servers = cfg.get("mcpServers", {})
    has_main = "vc-context" in servers
    has_meta = "vc-context-meta" in servers
    if has_main and has_meta:
        print("already")
        sys.exit(0)
    cfg.setdefault("mcpServers", {})
    if not has_main:
        cfg["mcpServers"]["vc-context"] = entry
    if not has_meta:
        cfg["mcpServers"]["vc-context-meta"] = meta_entry
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    print("merged")
else:
    path.write_text(json.dumps({"mcpServers": {"vc-context": entry, "vc-context-meta": meta_entry}}, indent=2) + "\n")
    print("created")
PY
)
    case "$STATUS" in
        created)  echo "🔌 Wrote $MCP_CONFIG — Claude Code auto-discovers vc-context + vc-context-meta." ;;
        merged)   echo "🔌 Added vc-context/vc-context-meta to existing $MCP_CONFIG." ;;
        already)  echo "ℹ️  $MCP_CONFIG already lists vc-context + vc-context-meta — left alone." ;;
        invalid)  echo "⚠️  $MCP_CONFIG is not valid JSON — skipped MCP wiring. Fix it and re-run." ;;
    esac

    # Enable both servers in .claude/settings.json (Claude Code allowlist).
    CLAUDE_SETTINGS=".claude/settings.json"
    if [ -f "$CLAUDE_SETTINGS" ]; then
        python3 - "$CLAUDE_SETTINGS" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    cfg = json.loads(path.read_text())
except json.JSONDecodeError:
    sys.exit(0)
enabled = cfg.get("enabledMcpjsonServers", [])
added = []
for name in ("vc-context", "vc-context-meta"):
    if name not in enabled:
        enabled.append(name)
        added.append(name)
if added:
    cfg["enabledMcpjsonServers"] = enabled
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    print(",".join(added))
PY
    fi
fi

# 5. Optional MCP-startup auto-reindex.
if $AUTO_REINDEX; then
    AUTO_REINDEX_SECONDS=$((AUTO_REINDEX_MINUTES * 60))
    python3 - ".vc-context/conventions.json" "$AUTO_REINDEX_SECONDS" <<'PY'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
interval = int(sys.argv[2])
path.parent.mkdir(parents=True, exist_ok=True)
if path.exists():
    try:
        cfg = json.loads(path.read_text())
    except json.JSONDecodeError:
        cfg = {}
else:
    cfg = {}
if not isinstance(cfg, dict):
    cfg = {}
cfg["auto_reindex"] = {
    "enabled": True,
    "interval_seconds": interval,
}
path.write_text(json.dumps(cfg, indent=2) + "\n")
PY
    echo "⏱️  Auto-reindex enabled: MCP startup rebuilds stale indexes after ${AUTO_REINDEX_MINUTES}m."
fi

# 6. Slash commands — copy curated set into .claude/commands/.
if ! $NO_COMMANDS; then
    SRC_COMMANDS="$BUILDER_DIR/templates/commands"
    DST_COMMANDS=".claude/commands"
    if [ -d "$SRC_COMMANDS" ]; then
        mkdir -p "$DST_COMMANDS"
        ADDED=0
        SKIPPED=0
        for src in "$SRC_COMMANDS"/*.md; do
            [ -f "$src" ] || continue
            name=$(basename "$src")
            dst="$DST_COMMANDS/$name"
            if [ -f "$dst" ] && ! $FORCE_COMMANDS; then
                SKIPPED=$((SKIPPED + 1))
            else
                cp "$src" "$dst"
                ADDED=$((ADDED + 1))
            fi
        done
        if [ $ADDED -gt 0 ]; then
            echo "📂 Slash commands: $ADDED added to $DST_COMMANDS/ (skipped $SKIPPED existing)."
        else
            echo "ℹ️  Slash commands: all $SKIPPED already present (use --force-commands to overwrite)."
        fi
    fi
fi

echo ""
echo "🎉 Done. What now:"
echo "   • Reload Claude Code so it picks up .mcp.json + .claude/commands/."
echo "   • Manual rebuild: python3 $RELATIVE_DIR/agent_map.py"
if $AUTO_REINDEX; then
    echo "   • Auto-reindex: enabled in .vc-context/conventions.json (${AUTO_REINDEX_MINUTES}m)."
else
    echo "   • Optional auto-reindex: ./$RELATIVE_DIR/install.sh --auto-reindex=60"
fi
if ! $SHARED; then
    echo "   • Switch to shared mode (commit artifacts): ./$RELATIVE_DIR/install.sh --shared"
fi
