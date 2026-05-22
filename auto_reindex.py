"""Optional MCP-startup reindexing.

Projects opt in via ``.vc-context/conventions.json``:

```json
{
  "auto_reindex": {"enabled": true, "interval_seconds": 1800}
}
```

When enabled, the MCP server checks the mtime of ``agent_root.json`` at
startup and runs ``agent_map.py`` only when the index is missing or older
than the configured interval.  This keeps the behavior tied to normal MCP
startup, so any agent that launches the server gets fresh-enough indexes
without needing a separate scheduler.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from paths import index_read_path

DEFAULT_INTERVAL_SECONDS = 3600
MIN_INTERVAL_SECONDS = 60


def _load_config(project_root: str) -> Dict[str, Any]:
    path = os.path.join(project_root, ".vc-context", "conventions.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cfg = data.get("auto_reindex")
    return cfg if isinstance(cfg, dict) else {}


def _interval_seconds(cfg: Dict[str, Any]) -> int:
    raw = cfg.get("interval_seconds", cfg.get("interval_sec", DEFAULT_INTERVAL_SECONDS))
    try:
        interval = int(raw)
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, interval)


def should_auto_reindex(project_root: str, *, now: Optional[float] = None) -> bool:
    """Return whether MCP startup should rebuild the index."""
    cfg = _load_config(project_root)
    if not cfg.get("enabled"):
        return False

    root_path = index_read_path(project_root, "agent_root.json")
    if not os.path.isfile(root_path):
        return True

    now = time.time() if now is None else now
    try:
        age = now - os.path.getmtime(root_path)
    except OSError:
        return True
    return age >= _interval_seconds(cfg)


def maybe_auto_reindex(project_root: str) -> Dict[str, Any]:
    """Rebuild when opted in and stale.

    Returns a tiny status dict for tests/diagnostics.  The MCP server
    intentionally ignores this payload so stdio stays reserved for
    JSON-RPC frames.
    """
    if not should_auto_reindex(project_root):
        return {"ok": True, "ran": False}

    builder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_map.py")
    if not os.path.isfile(builder):
        return {"ok": False, "ran": False, "error": f"agent_map.py not found at {builder}"}

    proc = subprocess.run(
        [sys.executable, builder, "--root", project_root],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return {
        "ok": proc.returncode == 0,
        "ran": True,
        "returncode": proc.returncode,
        "stderr_tail": proc.stderr[-1000:],
    }
