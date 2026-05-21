"""Project settings backup and restore.

Backs up only the human-authored configuration files — AGENTS.md,
CLAUDE.md, playbooks, conventions, MCP config, custom slash commands.
Never touches generated artifacts (agent_*.json, _module_map.json)
or metrics. The resulting ZIP is small enough to email or share via
any channel; it carries no source code.

CLI usage (via vc-context backup / restore):
    vc-context backup [--out backup.zip] [--root /path/to/project]
    vc-context restore backup.zip [--dry-run] [--force] [--root /path/to/project]

MCP tool:
    export_config()   → {path, manifest}
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# What gets backed up
# ---------------------------------------------------------------------------

# Files/dirs relative to project_root that are always included if present.
_STATIC_INCLUDES: List[str] = [
    ".vc-context/conventions.json",
    ".mcp.json",
    ".claude/mcp.json",
]

# Directory trees to walk and include all files.
_DIR_INCLUDES: List[str] = [
    ".ai-context/playbooks",
    ".ai-context/docs",
    ".claude/commands",
]

# Filename patterns scanned recursively across the whole project.
_RECURSIVE_FILENAMES: List[str] = [
    "AGENTS.md",
    "CLAUDE.md",
]

# Directories to skip during the recursive filename scan.
_SKIP_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".angular",
    "dist",
    "build",
    ".ai-context",  # handled separately via _DIR_INCLUDES
}


def _collect_files(project_root: str) -> List[Tuple[str, str]]:
    """Return [(abs_path, archive_path)] for every file to back up.

    archive_path is relative to project_root so the ZIP is portable
    across machines with different absolute paths.
    """
    collected: List[Tuple[str, str]] = []
    seen: set = set()

    def _add(abs_path: str) -> None:
        norm = os.path.normpath(abs_path)
        if norm in seen or not os.path.isfile(norm):
            return
        seen.add(norm)
        rel = os.path.relpath(norm, project_root).replace("\\", "/")
        collected.append((norm, rel))

    # Static files
    for rel in _STATIC_INCLUDES:
        _add(os.path.join(project_root, rel))

    # Directory trees
    for dir_rel in _DIR_INCLUDES:
        dir_abs = os.path.join(project_root, dir_rel)
        if not os.path.isdir(dir_abs):
            continue
        for dirpath, dirs, files in os.walk(dir_abs):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fname in files:
                _add(os.path.join(dirpath, fname))

    # Recursive filename scan (AGENTS.md, CLAUDE.md everywhere)
    for dirpath, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if fname in _RECURSIVE_FILENAMES:
                _add(os.path.join(dirpath, fname))

    collected.sort(key=lambda x: x[1])
    return collected


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def backup(
    project_root: str,
    out_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a ZIP backup of project settings.

    Returns a manifest dict:
        {path, size_bytes, files: [{archive_path, size_bytes}], created_at}
    """
    project_root = os.path.abspath(project_root)
    files = _collect_files(project_root)

    if not out_path:
        project_name = os.path.basename(project_root)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_path = os.path.join(project_root, f"vc-context-backup-{project_name}-{ts}.zip")

    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    manifest_files = []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for abs_path, arc_path in files:
            try:
                zf.write(abs_path, arc_path)
                manifest_files.append(
                    {"path": arc_path, "size_bytes": os.path.getsize(abs_path)}
                )
            except OSError:
                pass

        # Embed the manifest inside the ZIP itself
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "project_root": project_root,
            "project_name": os.path.basename(project_root),
            "vc_context_version": _vc_context_version(),
            "files": manifest_files,
        }
        zf.writestr("vc-context-manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    return {
        "path": out_path,
        "size_bytes": os.path.getsize(out_path),
        "files": manifest_files,
        "created_at": manifest["created_at"],
    }


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(
    backup_path: str,
    project_root: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """Restore project settings from a ZIP backup.

    Returns a result dict:
        {restored: [...], skipped: [...], conflicts: [...], dry_run: bool}

    Conflicts (files that already exist) are skipped unless --force.
    """
    backup_path = os.path.abspath(backup_path)
    project_root = os.path.abspath(project_root)

    if not os.path.isfile(backup_path):
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    restored: List[str] = []
    skipped: List[str] = []
    conflicts: List[str] = []

    with zipfile.ZipFile(backup_path, "r") as zf:
        names = zf.namelist()
        manifest_data: Optional[Dict[str, Any]] = None
        if "vc-context-manifest.json" in names:
            manifest_data = json.loads(zf.read("vc-context-manifest.json"))

        for arc_path in names:
            if arc_path == "vc-context-manifest.json":
                continue

            dest = os.path.join(project_root, arc_path)

            if os.path.exists(dest) and not force:
                conflicts.append(arc_path)
                skipped.append(arc_path)
                continue

            if not dry_run:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(arc_path) as src, open(dest, "wb") as dst:
                    dst.write(src.read())

            restored.append(arc_path)

    return {
        "restored": restored,
        "skipped": skipped,
        "conflicts": conflicts,
        "dry_run": dry_run,
        "source": backup_path,
        "target": project_root,
        "manifest": manifest_data,
    }


# ---------------------------------------------------------------------------
# Inspect (what would be backed up / what's inside a ZIP)
# ---------------------------------------------------------------------------


def inspect_backup(backup_path: str) -> Dict[str, Any]:
    """Return the manifest + file list from an existing backup ZIP."""
    backup_path = os.path.abspath(backup_path)
    if not os.path.isfile(backup_path):
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    with zipfile.ZipFile(backup_path, "r") as zf:
        names = zf.namelist()
        manifest: Optional[Dict[str, Any]] = None
        if "vc-context-manifest.json" in names:
            manifest = json.loads(zf.read("vc-context-manifest.json"))
        infos = [
            {"path": i.filename, "size_bytes": i.file_size}
            for i in zf.infolist()
            if i.filename != "vc-context-manifest.json"
        ]

    return {
        "path": backup_path,
        "size_bytes": os.path.getsize(backup_path),
        "files": infos,
        "manifest": manifest,
    }


def preview_backup(project_root: str) -> Dict[str, Any]:
    """Return what *would* be included without writing anything."""
    project_root = os.path.abspath(project_root)
    files = _collect_files(project_root)
    total = sum(os.path.getsize(a) for a, _ in files if os.path.isfile(a))
    return {
        "project_root": project_root,
        "file_count": len(files),
        "total_bytes": total,
        "files": [{"path": r, "size_bytes": os.path.getsize(a)} for a, r in files],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vc_context_version() -> str:
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        rpc = os.path.join(here, "mcp", "rpc.py")
        with open(rpc) as f:
            for line in f:
                if "SERVER_VERSION" in line and "=" in line:
                    return line.split("=")[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "unknown"
