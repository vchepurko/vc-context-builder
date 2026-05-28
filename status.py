"""Project index status report.

Python callable:  ``from status import get_status; get_status(project_root)``
CLI:              ``vc-context status [--root .] [--json]``
MCP tool:         ``status``

Returns a structured dict covering:
- index     : last build time, age, staleness, symbol count, auto_reindex config
- embeddings: provider name, model, SQLite existence + size + indexed symbols
- chat      : provider name, model, reachable flag
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Helpers — import only stdlib + sibling modules
# ---------------------------------------------------------------------------

# Keep imports lazy where the sibling lives in a different directory layer
# (the dispatcher adds _PARENT to sys.path, CLI and direct calls require the
# same; we rely on callers having done that before importing this module).


def _index_info(project_root: str) -> Dict[str, Any]:
    """Return information about agent_root.json (the primary index file)."""
    from auto_reindex import _interval_seconds, _load_config, should_auto_reindex
    from paths import index_read_path

    root_path = index_read_path(project_root, "agent_root.json")
    exists = os.path.isfile(root_path)

    last_built: Optional[str] = None
    age_seconds: Optional[float] = None
    stale: bool = True
    symbols_count: int = 0

    if exists:
        mtime = os.path.getmtime(root_path)
        age_seconds = round(time.time() - mtime, 1)
        last_built = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        stale = should_auto_reindex(project_root)

        # Count symbols from agent_symbols.json
        from paths import index_read_path as irp
        sym_path = irp(project_root, "agent_symbols.json")
        if os.path.isfile(sym_path):
            try:
                with open(sym_path, encoding="utf-8") as fh:
                    data = json.load(fh)
                symbols_count = len(data) if isinstance(data, dict) else 0
            except (OSError, json.JSONDecodeError):
                pass

    cfg = _load_config(project_root)
    auto_reindex = {
        "enabled": bool(cfg.get("enabled", False)),
        "interval_seconds": _interval_seconds(cfg) if cfg else 3600,
    }

    return {
        "index_file": root_path,
        "exists": exists,
        "last_built": last_built,
        "age_seconds": age_seconds,
        "stale": stale,
        "symbols_count": symbols_count,
        "auto_reindex": auto_reindex,
    }


def _embeddings_info(project_root: str) -> Dict[str, Any]:
    """Return information about the semantic embedding layer."""
    from semantic_store import db_path, provider_from_conventions

    # Provider
    try:
        provider = provider_from_conventions(project_root)
        provider_name: str = provider.name
        model: Optional[str] = (
            getattr(provider, "model_name", None) or getattr(provider, "model", None)
        )
    except Exception:
        provider_name = "unknown"
        model = None

    # SQLite
    db = db_path(project_root)
    sqlite_exists = os.path.isfile(db)
    sqlite_size_bytes: Optional[int] = None
    symbols_indexed: Optional[int] = None

    if sqlite_exists:
        try:
            sqlite_size_bytes = os.path.getsize(db)
        except OSError:
            pass
        try:
            conn = sqlite3.connect(db, timeout=5)
            try:
                row = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
                symbols_indexed = row[0] if row else 0
            except sqlite3.OperationalError:
                symbols_indexed = 0
            finally:
                conn.close()
        except Exception:
            pass

    return {
        "provider": provider_name,
        "model": model,
        "sqlite_path": db,
        "sqlite_exists": sqlite_exists,
        "sqlite_size_bytes": sqlite_size_bytes,
        "symbols_indexed": symbols_indexed,
    }


def _chat_info(project_root: str) -> Dict[str, Any]:
    """Return information about the configured chat provider."""
    try:
        from ollama_chat import chat_provider_from_conventions  # type: ignore[import-not-found]
    except ImportError:
        return {"configured": False}

    provider = chat_provider_from_conventions(project_root)
    if provider is None:
        return {"configured": False}

    info: Dict[str, Any] = {
        "configured": True,
        "provider": "ollama",
        "model": provider.model,
        "host": provider.host,
        "reachable": False,
    }
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{provider.host}/api/tags",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            tags = json.loads(resp.read().decode())
        names = [m.get("name", "") for m in tags.get("models", [])]
        info["reachable"] = True
        info["model_available"] = any(
            n == provider.model or n.startswith(provider.model.split(":")[0])
            for n in names
        )
    except Exception:
        pass

    return info


def get_status(project_root: str) -> Dict[str, Any]:
    """Return a full status snapshot for the given project root.

    Always safe to call — errors within sub-sections are caught and
    reported as ``{"error": "..."}`` inside the relevant key so that
    partial information is still returned.
    """
    project_root = os.path.abspath(project_root)

    index: Dict[str, Any]
    try:
        index = _index_info(project_root)
    except Exception as exc:
        index = {"error": str(exc)}

    embeddings: Dict[str, Any]
    try:
        embeddings = _embeddings_info(project_root)
    except Exception as exc:
        embeddings = {"error": str(exc)}

    chat: Dict[str, Any]
    try:
        chat = _chat_info(project_root)
    except Exception as exc:
        chat = {"error": str(exc)}

    return {
        "project_root": project_root,
        "index": index,
        "embeddings": embeddings,
        "chat": chat,
    }
