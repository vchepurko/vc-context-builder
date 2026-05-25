"""Centralised path resolution for vc-context artefacts.

Before this module, every writer hard-coded its own ``os.path.join(
project_root, "agent_X.json")`` and every reader (mostly
``query_engine.py``) did the same. Adding a new index file required
edits in two places (writer + reader) AND the file ended up cluttering
the consumer project's root next to ``README.md``.

We now route every artefact through one helper that knows about the
target directory and the backward-compat fallback. Layout:

    <project_root>/
        .vc-context/
            index/
                agent_root.json
                agent_symbols.json
                agent_tests.json
                ... etc.
            _parse_cache.json        (cache, separate kind of state)
            conventions.json         (config, separate kind)

The legacy location (``<project_root>/agent_X.json`` at the bare root)
is supported for **reads only** — `index_path()` returns the
``.vc-context/index/`` location for writes, and `index_read_path()`
falls back to the legacy root path when the new location doesn't
exist yet. Consumers calling agent_map.py once after pulling this
change land everything in the new spot; the fallback covers the
gap before that first run.
"""

from __future__ import annotations

import hashlib
import os

# Target directory for all agent_*.json indexes, relative to project
# root. ``.vc-context/`` already houses ``_parse_cache.json`` and
# ``conventions.json`` so this slots in naturally.
INDEX_DIR_NAME = os.path.join(".vc-context", "index")


def repo_hash(project_root: str) -> str:
    """Stable, short hash for per-repo local state directories."""
    p = os.path.abspath(project_root).encode("utf-8")
    return hashlib.sha1(p).hexdigest()[:8]


def index_dir(project_root: str) -> str:
    """Absolute path of the index directory under ``project_root``."""
    return os.path.join(project_root, INDEX_DIR_NAME)


def index_path(project_root: str, filename: str) -> str:
    """Canonical write path for an index artefact.

    Callers writing a new index file (agent_map.py and friends) use
    this — they should also ``os.makedirs(index_dir(...), exist_ok=
    True)`` before writing.
    """
    return os.path.join(index_dir(project_root), filename)


def index_read_path(project_root: str, filename: str) -> str:
    """Best-available read path — prefers the new location, falls
    back to the legacy root if the new file doesn't exist.

    Used by readers (``query_engine``, etc.) so a freshly pulled
    checkout that hasn't yet run ``agent_map.py`` still loads the
    old root-level indexes. Once the next ``agent_map.py`` run
    completes, the legacy files become obsolete (the writer side
    only emits to the new location).
    """
    new_path = index_path(project_root, filename)
    if os.path.exists(new_path):
        return new_path
    legacy = os.path.join(project_root, filename)
    return legacy


def ensure_index_dir(project_root: str) -> str:
    """Create the index directory (idempotent). Returns its path."""
    path = index_dir(project_root)
    os.makedirs(path, exist_ok=True)
    return path


def local_state_dir(project_root: str) -> str:
    """Per-repo local state root under ``~/.vc-context/<repo-hash>``.

    This is intentionally outside the project checkout: semantic vectors,
    learned decisions, and other regenerated/private data should not dirty
    production repos or leak across projects.
    """
    base = os.environ.get("VC_CONTEXT_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".vc-context")
    return os.path.join(base, repo_hash(project_root))


def ensure_local_state_dir(project_root: str, *parts: str) -> str:
    """Create and return a subdirectory of the per-repo local state root."""
    path = os.path.join(local_state_dir(project_root), *parts)
    os.makedirs(path, exist_ok=True)
    return path
