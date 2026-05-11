"""One-call DevOps snapshot — Docker / Caddy / GitHub Actions / scheduler jobs.

Closes the "where does deploy actually live?" investigation that today
scatters across `cat docker-compose.yml`, `grep restart Caddyfile`,
`ls .github/workflows/`, and a separate role-query for APScheduler.
Returns a single dict an agent can read once and reason from.

Design constraints
------------------
* **Stdlib only.** Submodule policy — no PyYAML, no third-party
  regex libs. compose / Caddyfile / Dockerfile parsers below are
  shallow regex/line-walk; they extract the fields agents actually
  ask about (service image / ports / restart, host → upstream,
  FROM / EXPOSE / CMD) and ignore the rest.
* **Lossy by design.** Anchors / merge-keys / multi-doc YAML aren't
  supported. For projects that need full fidelity, callers fall back
  to ``read_slice`` on the file.
* **Robust against missing files.** Empty list / null fields when an
  artefact isn't present — many small projects have only a Dockerfile
  + a single workflow.

Output shape (see :func:`build`)::

    {
      "compose_files": [{"path": "docker-compose.yml",
                         "services": [{"name": "bot",
                                       "image": "...",
                                       "build": ".",
                                       "ports": ["8080:8080"],
                                       "restart": "unless-stopped",
                                       "env_file": [".env"]}, ...]}, ...],
      "dockerfiles": [{"path": "Dockerfile",
                       "from": "python:3.13-slim",
                       "expose": [8080],
                       "entrypoint": "...",
                       "cmd": "..."}, ...],
      "caddy_sites": [{"file": "deploy/Caddyfile",
                       "domain": "shop.example.com",
                       "upstreams": ["bot:8080"]}],
      "workflows": [".github/workflows/ci.yml", ...],
      "scheduler_jobs": ["close_expired_auctions", ...],
    }
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

# Limits so we can't blow up the engine if someone drops a 50 MB YAML
# into the repo. Real compose files are <10 KB.
_MAX_BYTES_PER_FILE = 256 * 1024
_MAX_FILES_PER_KIND = 50


def build(project_root: str, scheduler_jobs: Optional[List[str]] = None) -> Dict[str, Any]:
    """Assemble the DevOps snapshot.

    Parameters
    ----------
    project_root:
        Absolute project root. Same conventions as ``configs_scanner``.
    scheduler_jobs:
        Pre-fetched list of APScheduler / scheduler-job role members.
        Caller (``QueryEngine``) supplies these because the role index
        already lives in ``agent_root.json`` — avoids re-parsing it
        here. Pass ``None`` / ``[]`` when there's no role index.

    Returns
    -------
    dict — see module docstring for the exact shape.
    """
    project_root = os.path.abspath(project_root)
    return {
        "compose_files": _collect_compose(project_root),
        "dockerfiles": _collect_dockerfiles(project_root),
        "caddy_sites": _collect_caddy(project_root),
        "workflows": _collect_workflows(project_root),
        "scheduler_jobs": list(scheduler_jobs or []),
    }


# ─── Walker shared across parsers ─────────────────────────────


_IGNORE_DIRS = frozenset(
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


def _walk(project_root: str):
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        for name in filenames:
            yield dirpath, name


def _read(abs_path: str) -> Optional[str]:
    try:
        if os.path.getsize(abs_path) > _MAX_BYTES_PER_FILE:
            return None
    except OSError:
        return None
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _rel(project_root: str, abs_path: str) -> str:
    return os.path.relpath(abs_path, project_root)


# ─── docker-compose ────────────────────────────────────────────

# Regex-based YAML walker tailored to compose v2/v3. We exploit the
# canonical 2-space indent and the fact that we only care about
# top-level ``services:`` and one-level-deep service keys.
_COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")

_SERVICE_KEYS = ("image", "build", "restart", "container_name")


def _collect_compose(project_root: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for dirpath, name in _walk(project_root):
        if name not in _COMPOSE_NAMES:
            continue
        if len(out) >= _MAX_FILES_PER_KIND:
            break
        abs_path = os.path.join(dirpath, name)
        text = _read(abs_path)
        if text is None:
            continue
        services = _parse_compose_services(text)
        out.append({"path": _rel(project_root, abs_path), "services": services})
    return out


def _parse_compose_services(text: str) -> List[Dict[str, Any]]:
    """Extract a list of services with the few fields agents actually
    query. Ignores volumes, networks, configs, secrets at top level —
    they're indirection layers, not the actual deployable artefact.
    """
    lines = text.splitlines()
    services: List[Dict[str, Any]] = []

    # Find the ``services:`` block boundaries.
    in_services = False
    services_indent: Optional[int] = None
    current: Optional[Dict[str, Any]] = None
    current_indent: Optional[int] = None
    current_list_key: Optional[str] = None  # tracks `ports:` / `env_file:`

    for raw in lines:
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if not in_services:
            if stripped == "services:" and indent == 0:
                in_services = True
                services_indent = 0
            continue

        # We've left the services block.
        if services_indent is not None and indent == 0 and not stripped.startswith("services:"):
            break

        # Service header (one level deeper than ``services:``).
        if (
            services_indent is not None
            and indent == services_indent + 2
            and stripped.endswith(":")
            and ":" in stripped
        ):
            name = stripped[:-1].strip()
            if name and not name.startswith("#"):
                current = {"name": name}
                services.append(current)
                current_indent = indent
                current_list_key = None
                continue

        # Inside a service: scalar fields.
        if current is not None and current_indent is not None and indent > current_indent:
            # End any open list.
            if not stripped.startswith("- "):
                current_list_key = None
            # Scalar key: value.
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", stripped)
            if m:
                key, value = m.group(1), m.group(2).strip()
                if key in _SERVICE_KEYS and value:
                    current[key] = _unquote(value)
                elif key in ("ports", "env_file", "volumes") and not value:
                    # Block-list follows.
                    current.setdefault(key, [])
                    current_list_key = key
                continue
            # List item under a known block-list.
            if stripped.startswith("- ") and current_list_key:
                item = _unquote(stripped[2:].strip())
                if item:
                    current[current_list_key].append(item)

    return services


def _unquote(value: str) -> str:
    """Strip surrounding YAML quotes — agents care about the value, not
    the quoting style."""
    v = value.strip()
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    # Drop trailing inline comment.
    if "#" in v:
        v = v.split("#", 1)[0].rstrip()
    return v


# ─── Dockerfile ────────────────────────────────────────────────


def _collect_dockerfiles(project_root: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for dirpath, name in _walk(project_root):
        if not (name == "Dockerfile" or name.startswith("Dockerfile.")):
            continue
        if len(out) >= _MAX_FILES_PER_KIND:
            break
        abs_path = os.path.join(dirpath, name)
        text = _read(abs_path)
        if text is None:
            continue
        out.append({"path": _rel(project_root, abs_path), **_parse_dockerfile(text)})
    return out


def _parse_dockerfile(text: str) -> Dict[str, Any]:
    """Extract the headline directives: FROM (last one wins for
    multi-stage), EXPOSE list, ENTRYPOINT, CMD."""
    from_value: Optional[str] = None
    expose: List[int] = []
    entrypoint: Optional[str] = None
    cmd: Optional[str] = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        upper = line.upper()
        if upper.startswith("FROM "):
            from_value = line[5:].strip()
        elif upper.startswith("EXPOSE "):
            for tok in line[7:].split():
                try:
                    expose.append(int(tok.split("/")[0]))
                except ValueError:
                    pass
        elif upper.startswith("ENTRYPOINT "):
            entrypoint = line[len("ENTRYPOINT ") :].strip()
        elif upper.startswith("CMD "):
            cmd = line[4:].strip()

    return {
        "from": from_value,
        "expose": expose,
        "entrypoint": entrypoint,
        "cmd": cmd,
    }


# ─── Caddyfile ─────────────────────────────────────────────────

_CADDY_NAMES = ("Caddyfile",)


def _collect_caddy(project_root: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for dirpath, name in _walk(project_root):
        if not (name in _CADDY_NAMES or name.startswith("Caddyfile.")):
            continue
        if len(out) >= _MAX_FILES_PER_KIND:
            break
        abs_path = os.path.join(dirpath, name)
        text = _read(abs_path)
        if text is None:
            continue
        for site in _parse_caddyfile(text):
            out.append({"file": _rel(project_root, abs_path), **site})
    return out


_CADDY_SITE_RE = re.compile(
    r"""(?P<host>[A-Za-z0-9_.${}*\-:\s,]+?)\s*\{(?P<body>[^{}]*)\}""",
    re.DOTALL,
)
_REVERSE_PROXY_RE = re.compile(r"reverse_proxy\s+(.+)")


def _parse_caddyfile(text: str) -> List[Dict[str, Any]]:
    """Top-level site blocks only. Nested blocks (header { … }) are
    consumed greedily as part of ``body`` but we only grep for
    ``reverse_proxy`` lines so nesting doesn't break extraction."""
    sites: List[Dict[str, Any]] = []
    # Remove comments first so braces inside ``# …`` don't confuse us.
    cleaned = re.sub(r"#.*$", "", text, flags=re.MULTILINE)
    # Iterate matching top-level site blocks. The regex above is a
    # heuristic — Caddyfile grammar isn't strictly regex-friendly, but
    # the common shape (domain { ... }) covers what's deployed.
    pos = 0
    while pos < len(cleaned):
        m = re.search(r"(?P<host>[^{}\n]+?)\s*\{", cleaned[pos:])
        if not m:
            break
        host_raw = m.group("host").strip()
        # Find matching closing brace (one level of nesting allowed).
        body_start = pos + m.end()
        depth = 1
        i = body_start
        while i < len(cleaned) and depth:
            ch = cleaned[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        if depth:
            break
        body = cleaned[body_start : i - 1]
        upstreams: List[str] = []
        for rp in _REVERSE_PROXY_RE.finditer(body):
            upstreams.extend(tok for tok in rp.group(1).split() if not tok.startswith("{"))
        # ``host_raw`` can be ``a.com, b.com`` (Caddy allows multi-host).
        for host in [h.strip() for h in host_raw.replace(",", " ").split() if h.strip()]:
            sites.append({"domain": host, "upstreams": upstreams})
        pos = i
    return sites


# ─── GitHub Actions workflows ──────────────────────────────────


def _collect_workflows(project_root: str) -> List[str]:
    workflows_dir = os.path.join(project_root, ".github", "workflows")
    if not os.path.isdir(workflows_dir):
        return []
    out: List[str] = []
    for name in sorted(os.listdir(workflows_dir)):
        if name.endswith((".yml", ".yaml")):
            out.append(os.path.join(".github", "workflows", name))
    return out
