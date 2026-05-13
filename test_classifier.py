"""Test categorisation — split unit vs. integration vs. unknown.

Static analysis of every ``test_*.py`` under the project root: each file
gets a category based on the imports it pulls in and the markers it
declares. The whole point: let ``pytest`` (and pre-commit) skip slow
integration tests by default while still keeping a fast lookup of which
files form the integration suite.

Categories
----------
* ``integration`` — file imports something that touches a real
  HTTP/DB/queue boundary (e.g. ``httpx.ASGITransport``, ``async_session``,
  ``aiohttp.ClientSession``, ``psycopg``, ``redis``...) OR carries a
  ``@pytest.mark.integration`` decorator / module-level
  ``pytestmark = pytest.mark.integration``.
* ``unit`` — file imports only ``unittest.mock`` / typing / dataclasses /
  the project under test, no I/O fixtures.
* ``unknown`` — neither set fired (e.g. fixture-only module). Lets the
  caller decide what to do — usually treat as ``unit``-safe.

Output
------
``agent_test_categories.json`` ::

    {
      "tests/test_backend_admin_staff.py": {
        "category": "integration",
        "signals": ["httpx.ASGITransport", "set_test_app"]
      },
      "tests/test_admin_staff_handler.py": {
        "category": "unit",
        "signals": ["unittest.mock"]
      }
    }

Stdlib only.
"""

from __future__ import annotations

import ast
import json
import os
from collections.abc import Iterable
from typing import Any, Dict, List, Optional, Set, Tuple

TEST_CATEGORIES_FILENAME = "agent_test_categories.json"

IGNORE_DIRS = {
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
    ".ai-context",
    ".vc-context",
}

# Substrings that, when present in any import target, flag the file as
# integration. Both module names (``aiohttp``) and dotted attributes
# (``httpx.ASGITransport``) are matched as substrings of the canonical
# textual form returned by ``_import_targets``.
_INTEGRATION_IMPORT_HINTS: Tuple[str, ...] = (
    # HTTP / ASGI clients hitting a real wire or in-process app
    "httpx.ASGITransport",
    "httpx.AsyncClient",
    "aiohttp.ClientSession",
    "fastapi.testclient",
    "requests",
    # Test-app shim that boots the FastAPI app under ASGITransport
    "bot.api_client.set_test_app",
    "bot.api_client.clear_test_app",
    # Direct DB session usage (vs. a mock)
    "database.db.async_session",
    "database.db.engine",
    "sqlalchemy.ext.asyncio",
    "asyncpg",
    "psycopg",
    "psycopg2",
    "aiosqlite",
    # Out-of-process services
    "redis",
    "aioredis",
    "kafka",
    "aiokafka",
    "motor",
    "boto3",
    "botocore",
    "smtplib",
    "aiosmtpd",
)

# Module-level `import x` or `from a.b import c` markers that flag a
# file as a unit test. These are checked AFTER integration hints, so a
# file that imports both `unittest.mock` and `httpx.ASGITransport` ends
# up `integration` (the more specific signal wins).
_UNIT_IMPORT_HINTS: Tuple[str, ...] = (
    "unittest.mock",
    "unittest.mock.AsyncMock",
    "unittest.mock.MagicMock",
    "unittest.mock.patch",
    "pytest_mock",
)


def _iter_test_files(project_root: str) -> Iterable[str]:
    """Yield every Python test file under ``project_root``.

    Convention: filename starts with ``test_``. Avoids treating
    ``conftest.py`` as a test (it's a fixture container).
    """
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if f.startswith("test_") and f.endswith(".py"):
                yield os.path.join(cur, f)


def _import_targets(tree: ast.AST) -> List[str]:
    """Return canonical text for every imported name in the file.

    For ``import a.b`` → ``["a.b"]``.
    For ``from a.b import c, d`` → ``["a.b", "a.b.c", "a.b.d"]`` (each
    target joined). The dotted forms make substring matches reliable —
    a hint like ``"httpx.ASGITransport"`` matches ``from httpx import
    ASGITransport`` and ``import httpx; httpx.ASGITransport(...)``
    alike.
    """
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base:
                out.append(base)
            for alias in node.names:
                if base:
                    out.append(f"{base}.{alias.name}")
                else:
                    out.append(alias.name)
    return out


def _has_integration_marker(tree: ast.AST) -> Optional[str]:
    """Return ``"@pytest.mark.integration"`` / ``"pytestmark="`` when
    the file declares the marker, else ``None``.

    Catches:
      * ``pytestmark = pytest.mark.integration``
      * ``pytestmark = [pytest.mark.integration, ...]``
      * any function decorated with ``@pytest.mark.integration``
    """

    def _is_integration_attr(node: ast.AST) -> bool:
        # pytest.mark.integration →
        #   Attribute(value=Attribute(value=Name('pytest'), attr='mark'), attr='integration')
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "integration"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "pytest"
        )

    # Top-level `pytestmark = pytest.mark.integration`
    for stmt in getattr(tree, "body", []):
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    if _is_integration_attr(stmt.value):
                        return "pytestmark"
                    if isinstance(stmt.value, (ast.List, ast.Tuple)):
                        for el in stmt.value.elts:
                            if _is_integration_attr(el):
                                return "pytestmark"
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in getattr(stmt, "decorator_list", []) or ():
                if isinstance(dec, ast.Call):
                    dec = dec.func
                if _is_integration_attr(dec):
                    return "@pytest.mark.integration"
    return None


def classify_test_file(file_path: str) -> Dict[str, Any]:
    """Return ``{category, signals}`` for a single test file.

    ``signals`` is a sorted, deduplicated list — the substrings/marker
    names that fired the classification. Empty list when ``unknown``.
    """
    try:
        with open(file_path, encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        return {"category": "unknown", "signals": []}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"category": "unknown", "signals": []}

    integration_signals: Set[str] = set()
    unit_signals: Set[str] = set()

    targets = _import_targets(tree)
    for tgt in targets:
        for hint in _INTEGRATION_IMPORT_HINTS:
            if hint in tgt:
                integration_signals.add(hint)
        for hint in _UNIT_IMPORT_HINTS:
            if hint in tgt:
                unit_signals.add(hint)

    marker = _has_integration_marker(tree)
    if marker is not None:
        integration_signals.add(marker)

    if integration_signals:
        return {"category": "integration", "signals": sorted(integration_signals)}
    if unit_signals:
        return {"category": "unit", "signals": sorted(unit_signals)}
    return {"category": "unknown", "signals": []}


def collect_test_categories(project_root: str) -> Dict[str, Dict[str, Any]]:
    """Walk every ``test_*.py`` and return ``{rel_path → {category, signals}}``.

    Paths are project-relative with forward slashes; sorted on write
    for deterministic artifacts.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for full in _iter_test_files(project_root):
        rel = os.path.relpath(full, project_root).replace(os.sep, "/")
        out[rel] = classify_test_file(full)
    return out


def write_test_categories(
    project_root: str,
    index: Dict[str, Dict[str, Any]],
) -> str:
    from paths import ensure_index_dir, index_path

    ensure_index_dir(project_root)
    out_path = index_path(project_root, TEST_CATEGORIES_FILENAME)
    ordered = {k: index[k] for k in sorted(index)}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return out_path


# ----------------------------------------------------------------------
# Lookup helpers (used by QueryEngine)
# ----------------------------------------------------------------------


def lookup_tests_by_category(
    index: Dict[str, Dict[str, Any]],
    category: str,
) -> List[str]:
    """Return file paths for the given category, sorted.

    Empty list for unknown category names — let callers print
    "no matching tests" without special-casing.
    """
    if not category:
        return []
    return sorted(
        path
        for path, rec in index.items()
        if isinstance(rec, dict) and rec.get("category") == category
    )


def category_summary(index: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    """``{category → count}`` over the whole index."""
    counts: Dict[str, int] = {}
    for rec in index.values():
        if not isinstance(rec, dict):
            continue
        cat = rec.get("category", "unknown")
        counts[cat] = counts.get(cat, 0) + 1
    return counts
