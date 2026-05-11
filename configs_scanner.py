"""Scanner for non-code config files — env / yaml / toml / Caddyfile / …

Closes a real gap observed in agent telemetry: questions like "where is
``GOOGLE_OAUTH_*`` referenced" or "which boot has SUPPORT_ID set" fall
back to Bash ``grep -rn`` today because the Python AST indexer ignores
non-code surfaces. This scanner is a tiny, stdlib-only, on-demand
substitute: walk the project, collect files matching declared "kinds",
return lines that match the requested pattern.

Design choices
--------------
* **No persistent index.** Files change rarely, projects are small
  (configs live in tens, not thousands of files), and rebuilding a
  sidecar JSON for them would add yet another artifact for the indexer
  to keep fresh. A one-shot walk + grep is fast enough (<50ms on
  typical repos).
* **Substring + regex, case-insensitive by default.** Mirrors what
  ``find_locale_key`` does — agents typically know roughly what name
  they're searching for ("FERNET"), not an exact spelling.
* **Kind whitelist.** Caller passes ``kinds=['env','yaml',…]`` so
  unrelated noise (e.g. ``locale.yml`` is yaml but unrelated to
  deployment) is filterable. Default = all known kinds.
* **Bounded output.** Always returns the file path + 1-based line
  number + raw line; never the full file body. Caller can ``read_slice``
  if they need surrounding context.

Stdlib only.
"""

from __future__ import annotations

import fnmatch
import os
import re
from typing import Any, Dict, FrozenSet, List, Optional

# (kind, list-of-glob-patterns). Order is informational only — matches
# all categories independently.
_KIND_PATTERNS: Dict[str, List[str]] = {
    # Environment files: .env, .env.example, .env.production, …
    "env": [".env", ".env.*", "*.env"],
    # YAML configs — docker-compose, github actions, kubernetes, etc.
    "yaml": ["*.yml", "*.yaml"],
    # TOML — pyproject, Cargo, Pipfile.lock(toml), generic.
    "toml": ["*.toml"],
    # INI/CFG — alembic.ini, setup.cfg, .pre-commit-config (toml-y but
    # close enough for grep), tox.ini.
    "ini": ["*.ini", "*.cfg"],
    # Generic nginx-style and Caddy. Caddyfile has no extension by
    # convention; match it explicitly.
    "caddy": ["Caddyfile", "Caddyfile.*"],
    "nginx": ["nginx.conf", "*.nginx.conf"],
    # Catch-all for arbitrary .conf — sshd_config-style names live
    # here too if user passes ``kind='conf'``.
    "conf": ["*.conf"],
    # JSON configs (mcp.json, package.json, tsconfig.json) — narrow
    # to common settings filenames so we don't pull every JSON in repo.
    "json": [
        ".mcp.json",
        "mcp.json",
        "package.json",
        "tsconfig.json",
        "pyrightconfig.json",
        "settings.json",
    ],
    # Dockerfiles + variants (Dockerfile.prod, Dockerfile.dev).
    "dockerfile": ["Dockerfile", "Dockerfile.*"],
    # GitHub Actions workflow files (already covered by yaml, but
    # listed separately so callers can target CI specifically).
    "github-actions": [".github/workflows/*.yml", ".github/workflows/*.yaml"],
}

ALL_KINDS: FrozenSet[str] = frozenset(_KIND_PATTERNS)


# Directories never worth scanning — mirrors QueryEngine.IGNORE_DIRS but
# kept local so the scanner module is importable without the engine.
_IGNORE_DIRS: FrozenSet[str] = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "__pycache__",
        "dist",
        "build",
        ".venv",
        "venv",
        ".idea",
        ".vscode",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
    }
)

# Max bytes we'll read from a single file. Configs are tiny in practice;
# this guards against an accidental match on a large file dropped into
# the repo (e.g. a backup CSV named ``.env.backup``).
_MAX_BYTES_PER_FILE = 256 * 1024


def list_kinds() -> List[str]:
    """All known config kinds. Useful for ``--help``-style outputs."""
    return sorted(ALL_KINDS)


def _kind_of(rel_path: str, name: str, kinds: FrozenSet[str]) -> Optional[str]:
    """Return the first kind whose glob matches ``rel_path`` or ``name``,
    restricted to the caller-allowed ``kinds`` set."""
    for kind in kinds:
        for glob in _KIND_PATTERNS[kind]:
            # Path-anchored globs (``.github/workflows/*.yml``) match
            # against the full project-relative path; bare filename
            # globs (``*.yml``) match the basename.
            target = rel_path if "/" in glob else name
            if fnmatch.fnmatch(target, glob):
                return kind
    return None


def _iter_config_files(project_root: str, kinds: FrozenSet[str]):
    """Yield ``(rel_path, abs_path, kind)`` for every matching file."""
    project_root = os.path.abspath(project_root)
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            rel_path = os.path.relpath(abs_path, project_root)
            kind = _kind_of(rel_path, name, kinds)
            if kind is not None:
                yield rel_path, abs_path, kind


def scan(
    project_root: str,
    pattern: str,
    *,
    kinds: Optional[List[str]] = None,
    case_sensitive: bool = False,
    use_regex: bool = False,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Walk ``project_root`` for config files of ``kinds`` and return every
    line that matches ``pattern``.

    Parameters
    ----------
    project_root:
        Repository root. Walks recursively, respecting ``_IGNORE_DIRS``.
    pattern:
        Substring to look for; promoted to regex when ``use_regex=True``.
    kinds:
        Whitelist of config categories (see :func:`list_kinds`). ``None``
        = scan all known kinds.
    case_sensitive:
        Default ``False`` — agents usually search "FERNET" hoping to
        catch both ``FERNET_KEY`` and ``backup_fernet``.
    use_regex:
        When ``True``, ``pattern`` is compiled as a regex. Invalid regex
        raises :class:`re.error`.
    limit:
        Maximum number of matches to return. Older matches are kept;
        the call short-circuits once the limit is hit so a runaway
        pattern doesn't materialise thousands of rows.

    Returns
    -------
    list of dicts, one per match::

        {"file": "config.py", "line": 137, "kind": "yaml",
         "text": "GOOGLE_OAUTH_CLIENT_ID: str = ..."}

    Empty list when the pattern is empty or no files matched.
    """
    if not pattern:
        return []

    if kinds is None:
        active = ALL_KINDS
    else:
        # Silently drop unknown kinds — caller passes user input, we
        # don't want a typo'd ``"yam"`` to crash the whole scan.
        active = frozenset(k for k in kinds if k in ALL_KINDS)
    if not active:
        return []

    flags = 0 if case_sensitive else re.IGNORECASE
    if use_regex:
        matcher = re.compile(pattern, flags)
    else:
        # Plain substring; case-folding done up front when needed so
        # the inner loop just compares bytes.
        needle = pattern if case_sensitive else pattern.lower()

        def _is_match(line: str) -> bool:
            return needle in (line if case_sensitive else line.lower())

    out: List[Dict[str, Any]] = []
    for rel_path, abs_path, kind in _iter_config_files(project_root, active):
        try:
            size = os.path.getsize(abs_path)
        except OSError:
            continue
        if size > _MAX_BYTES_PER_FILE:
            continue
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as fh:
                for lineno, raw in enumerate(fh, start=1):
                    if use_regex:
                        hit = bool(matcher.search(raw))
                    else:
                        hit = _is_match(raw)
                    if not hit:
                        continue
                    # Trim trailing whitespace; keep leading whitespace
                    # so YAML structure is visible.
                    out.append(
                        {
                            "file": rel_path,
                            "line": lineno,
                            "kind": kind,
                            "text": raw.rstrip("\n").rstrip("\r"),
                        }
                    )
                    if len(out) >= limit:
                        return out
        except OSError:
            continue
    return out
