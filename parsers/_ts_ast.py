"""Optional TypeScript AST extractor for Angular decorator metadata.

Talks to ``parsers/_ts_ast_extractor.mjs`` over a persistent Node
worker. The first ``parse(file, project_root)`` call spawns the
worker; subsequent calls pipe file paths through its stdin and
receive one JSON line per file on stdout. Per-file cost drops from
~50 ms (process spawn) to ~1–3 ms (AST parse only), which closes
the lms-client "rebuild_index always times out" gap pinned in the
submodule ROADMAP.

Falls back to one-shot subprocess (legacy behaviour) when the
worker can't be started OR when called on a project whose worker
has died.

Why opt-in: vc-context's hard contract is "stdlib-only Python, zero
runtime deps". Node + typescript are deps of the *target* project,
not of vc-context, so we can rely on them only when the target
project already has them installed.

Enable in ``.vc-context/conventions.json``::

    {"typescript_ast": {"enabled": true}}

When unset / false, this module is never invoked — the regex path
remains the default and stays fast.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import threading
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_EXTRACTOR = os.path.join(_HERE, "_ts_ast_extractor.mjs")
_NODE_TIMEOUT_SEC = 5.0

# Cache available-or-not per project_root for the process lifetime —
# `shutil.which` and `os.path.isdir` are cheap but called per file
# during a full agent_map scan would still pile up.
_AVAIL_CACHE: Dict[str, bool] = {}

# Persistent worker subprocesses, one per project_root. Spawned lazily
# on the first parse() call; closed by atexit hook.
_WORKERS: Dict[str, _TSWorker] = {}  # populated lazily; class defined below
_WORKERS_LOCK = threading.Lock()


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
        with open(conv_path, encoding="utf-8") as fh:
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
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _AVAIL_CACHE[project_root] = False
        return False
    _AVAIL_CACHE[project_root] = proc.returncode == 0
    return _AVAIL_CACHE[project_root]


class _TSWorker:
    """Persistent Node subprocess running ``extractor.mjs --server``.

    One worker per project_root. Each ``parse(file_path)`` writes the
    path to stdin and reads one JSON line from stdout. The lock keeps
    concurrent callers from interleaving writes; reads are
    single-threaded.

    When the worker dies (Node crash, OS kill) we mark it ``dead``
    and the next call lazily respawns.
    """

    def __init__(self, project_root: str) -> None:
        self.project_root = project_root
        self.dead = False
        self.lock = threading.Lock()
        try:
            self.proc: Optional[subprocess.Popen] = subprocess.Popen(
                ["node", _EXTRACTOR, "--server", project_root],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # line-buffered text mode
            )
        except OSError:
            self.proc = None
            self.dead = True

    def parse(self, file_path: str) -> Optional[List[Dict[str, Any]]]:
        if self.dead or self.proc is None:
            return None
        if self.proc.poll() is not None:
            self.dead = True
            return None
        with self.lock:
            try:
                assert self.proc.stdin is not None and self.proc.stdout is not None
                self.proc.stdin.write(file_path + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
            except (OSError, BrokenPipeError, AssertionError):
                self.dead = True
                return None
        if not line:
            self.dead = True
            return None
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if isinstance(data, list):
            return data
        # Per-file errors come back as `{error: "..."}` — caller falls
        # back to the regex path for THAT file but the worker stays
        # alive for the rest.
        return None

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin is not None and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.proc.kill()
            except OSError:
                pass
        self.dead = True


def _get_worker(project_root: str) -> Optional[_TSWorker]:
    with _WORKERS_LOCK:
        worker = _WORKERS.get(project_root)
        if worker is not None and not worker.dead:
            return worker
        worker = _TSWorker(project_root)
        if worker.dead:
            return None
        _WORKERS[project_root] = worker
        return worker


def _close_all_workers() -> None:
    with _WORKERS_LOCK:
        for worker in list(_WORKERS.values()):
            worker.close()
        _WORKERS.clear()


atexit.register(_close_all_workers)


def parse(file_path: str, project_root: str) -> Optional[List[Dict[str, Any]]]:
    """Extract Angular decorator metadata from one TS file via the AST.

    Routes through the persistent worker (per project_root). On any
    worker failure, falls back to a one-shot subprocess that matches
    the original pre-batching contract.

    Returns a list of records (one per Angular class) on success, or
    ``None`` on any failure — the caller should fall back to regex.
    """
    if not is_available(project_root):
        return None
    worker = _get_worker(project_root)
    if worker is not None:
        result = worker.parse(file_path)
        if result is not None:
            return result
        # If the worker died mid-call, drop the cached reference so the
        # next invocation respawns instead of hammering a dead socket.
        if worker.dead:
            with _WORKERS_LOCK:
                _WORKERS.pop(project_root, None)
    # One-shot fallback (legacy path).
    try:
        proc = subprocess.run(
            ["node", _EXTRACTOR, file_path, project_root],
            capture_output=True,
            text=True,
            timeout=_NODE_TIMEOUT_SEC,
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
