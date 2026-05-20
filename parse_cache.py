"""File-level parse cache — skip re-parsing files whose ``(mtime, size)``
hasn't changed since the previous build.

The existing ``ContextBuilder._needs_update`` check rebuilds an
*entire directory* when any of its files changes; this cache layer
adds per-file granularity inside the rebuild.  When one file in a
20-file directory changes, only that file re-parses; the other 19
reuse cached ``{exports, dependencies}`` from disk.

Cache file lives at ``<project_root>/.vc-context/_parse_cache.json``
(gitignored).  Schema::

    {
        "version": 1,
        "epoch": "<sha256 of conventions.json + roles.json>",
        "entries": {
            "<rel_path>": {
                "mtime": <float>,
                "size": <int>,
                "result": {"exports": [...], "dependencies": [...]}
            }
        }
    }

Invalidation: the ``epoch`` is derived from the project's
configuration files (``.vc-context/conventions.json`` and
``.vc-context/roles.json``).  When either changes, the epoch differs
and every entry is treated as a miss — full rebuild.  A version
bump in this module also invalidates the cache so old cache files
from a previous schema don't bleed into a new run.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional

CACHE_DIR = ".vc-context"
CACHE_FILENAME = "_parse_cache.json"
CACHE_VERSION = 6  # v6: added enum + const-object symbol extraction for TypeScript

# Files whose mtime contributes to the cache epoch — when any of these
# changes, the cache is wholesale invalidated.  These are the configs
# that influence parser behaviour (custom roles, conventions, ignore
# overrides, etc.).
_EPOCH_FILES = (
    os.path.join(CACHE_DIR, "conventions.json"),
    os.path.join(CACHE_DIR, "roles.json"),
)


def _epoch(project_root: str) -> str:
    """Hash of the (mtime, size) tuple of every epoch-contributing file.

    Returns a stable string so cache files stay diffable / reproducible
    across rebuilds with the same config.
    """
    h = hashlib.sha256()
    h.update(f"v{CACHE_VERSION}".encode("ascii"))
    for rel in _EPOCH_FILES:
        path = os.path.join(project_root, rel)
        if os.path.isfile(path):
            try:
                stat = os.stat(path)
                h.update(f"|{rel}|{stat.st_mtime_ns}|{stat.st_size}".encode("ascii"))
            except OSError:
                pass
    return h.hexdigest()


def _cache_path(project_root: str) -> str:
    return os.path.join(project_root, CACHE_DIR, CACHE_FILENAME)


def load(project_root: str) -> Dict[str, Any]:
    """Read the cache from disk.  Returns an empty cache (with current
    epoch) on any failure — missing dir, malformed JSON, version
    mismatch, epoch mismatch.
    """
    current_epoch = _epoch(project_root)
    empty = {"version": CACHE_VERSION, "epoch": current_epoch, "entries": {}}
    path = _cache_path(project_root)
    if not os.path.isfile(path):
        return empty
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    if data.get("version") != CACHE_VERSION or data.get("epoch") != current_epoch:
        return empty
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return empty
    # Trust shape only loosely — entries with the wrong inner type just
    # miss on lookup, no need to scrub here.
    return {"version": CACHE_VERSION, "epoch": current_epoch, "entries": entries}


def save(project_root: str, cache: Dict[str, Any]) -> None:
    """Persist the cache to disk.  Creates the ``.vc-context/`` dir on
    demand.  Best-effort — failures are logged by the caller.
    """
    os.makedirs(os.path.join(project_root, CACHE_DIR), exist_ok=True)
    path = _cache_path(project_root)
    payload = {
        "version": CACHE_VERSION,
        "epoch": cache.get("epoch") or _epoch(project_root),
        "entries": cache.get("entries") or {},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def get(
    cache: Dict[str, Any],
    rel_path: str,
    abs_path: str,
) -> Optional[Dict[str, Any]]:
    """Look up the cached ``{exports, dependencies}`` payload for a file.

    Returns ``None`` (cache miss) when:
    - The file isn't in the cache at all.
    - The file's current ``(mtime, size)`` differs from the recorded
      values — content has changed since the last parse.
    """
    entries = cache.get("entries") or {}
    record = entries.get(rel_path)
    if not isinstance(record, dict):
        return None
    try:
        stat = os.stat(abs_path)
    except OSError:
        return None
    if record.get("mtime") != stat.st_mtime or record.get("size") != stat.st_size:
        return None
    result = record.get("result")
    return result if isinstance(result, dict) else None


def put(
    cache: Dict[str, Any],
    rel_path: str,
    abs_path: str,
    result: Dict[str, Any],
) -> None:
    """Store the parsed payload for a file under its current
    ``(mtime, size)``.  No-op when the file no longer exists at the
    given path (race against deletion mid-build)."""
    try:
        stat = os.stat(abs_path)
    except OSError:
        return
    entries = cache.setdefault("entries", {})
    entries[rel_path] = {
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "result": result,
    }


def prune(cache: Dict[str, Any], live_paths: set) -> None:
    """Drop cache entries whose source file is no longer indexed.

    Called once at the end of a build with the set of relative paths
    we just touched.  Keeps the cache file from growing unbounded
    when files are deleted from the project.
    """
    entries = cache.get("entries")
    if not isinstance(entries, dict):
        return
    stale = [k for k in entries if k not in live_paths]
    for k in stale:
        del entries[k]
