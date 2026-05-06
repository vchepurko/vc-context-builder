"""Optional TypeScript AST extractor for Angular decorator metadata.

Spawns ``node parsers/_ts_ast_extractor.mjs <file>`` against the
target project's local ``typescript`` install and parses the JSON
output. When Node or ``typescript`` is missing, ``parse`` returns
``None`` and the caller falls back to the regex parser.

Why opt-in: vc-context's hard contract is "stdlib-only Python, zero
runtime deps". Node + typescript are deps of the *target* project, not
of vc-context, so we can rely on them only when the target project
already has them installed. Detection is per-call, cheap (a single
``which node`` + a path stat); the result is cached for the process
lifetime.

Performance: each ``parse`` call spawns a Node process (~50 ms on a
warm cache). For now, agent_map.py invokes this only for files that
look like Angular sources (``.ts`` with a class containing a
``@Component`` / ``@Injectable`` / ``@Directive`` / ``@Pipe`` /
``@NgModule`` decorator marker). Batch mode is a future optimisation.

Enable in ``.vc-context/conventions.json``::

    {"typescript_ast": {"enabled": true}}

When unset / false, this module is never invoked — the regex path
remains the default and stays fast.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_EXTRACTOR = os.path.join(_HERE, "_ts_ast_extractor.mjs")
_NODE_TIMEOUT_SEC = 5.0

# Cache available-or-not per project_root for the process lifetime —
# `shutil.which` and `os.path.isdir` are cheap but called per file
# during a full agent_map scan would still pile up.
_AVAIL_CACHE: Dict[str, bool] = {}


def is_enabled(project_root: str) -> bool:
    """Cheap conventions.json check. Returns True only when the
    project explicitly opts in via
    ``{"typescript_ast": {"enabled": true}}``.

    Default is OFF so the regex path stays the zero-config behaviour.
    """
    conv_path = os.path.join(project_root, ".vc-context", "conventions.json")
    if not os.path.isfile(conv_path):
        return False
    try:
        with open(conv_path, "r", encoding="utf-8") as fh:
            conv = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    cfg = conv.get("typescript_ast") if isinstance(conv, dict) else None
    return bool(isinstance(cfg, dict) and cfg.get("enabled") is True)


def is_available(project_root: str) -> bool:
    """Detect whether Node and a usable ``typescript`` are present.

    Cached per project_root.  Safe to call from a hot loop.
    """
    cached = _AVAIL_CACHE.get(project_root)
    if cached is not None:
        return cached
    if shutil.which("node") is None:
        _AVAIL_CACHE[project_root] = False
        return False
    # Local install wins — but if the target uses a global typescript
    # (uncommon, but happens in mono-repos), we still try to dispatch
    # and let the JS extractor's import fall back to global.
    local_ts = os.path.join(project_root, "node_modules", "typescript", "package.json")
    if os.path.isfile(local_ts):
        _AVAIL_CACHE[project_root] = True
        return True
    # Heuristic for global: try a 1-shot probe — `node -e "require('typescript')"`.
    try:
        proc = subprocess.run(
            ["node", "-e", "require('typescript')"],
            capture_output=True, text=True, timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _AVAIL_CACHE[project_root] = False
        return False
    _AVAIL_CACHE[project_root] = proc.returncode == 0
    return _AVAIL_CACHE[project_root]


def parse(file_path: str, project_root: str) -> Optional[List[Dict[str, Any]]]:
    """Extract Angular decorator metadata from one TS file via the AST.

    Returns a list of records (one per Angular class) on success, or
    ``None`` on any failure — the caller should fall back to regex.
    Each record matches the shape emitted by ``_ts_ast_extractor.mjs``::

        {"name": "CartService", "role": "ng-service",
         "selector": null, "templateUrl": null, "styleUrls": [],
         "standalone": null, "providedIn": "root", "pipeName": null,
         "inputs": [], "outputs": []}
    """
    if not is_available(project_root):
        return None
    try:
        proc = subprocess.run(
            ["node", _EXTRACTOR, file_path, project_root],
            capture_output=True, text=True, timeout=_NODE_TIMEOUT_SEC,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None
