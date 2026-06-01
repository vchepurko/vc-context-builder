"""Tests for the Python HTTP-client call collector (Feature E).

Covers the full path: config loading from ``conventions.json`` →
AST extraction (inline + variable-bound shapes) → augmentation of
``agent_routes.json`` with ``callers_python`` → ``QueryEngine.route_callers``
returning a unified flat list with ``lang`` markers.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from indexers.http_callers import (
    attach_python_callers,
    collect_python_calls,
    load_http_clients,
)
from indexers.route_bridge import build_route_index, write_route_index
from query_engine import QueryEngine


def _write(path: str, body: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body))


class _Fixture:
    """Build a synthetic project tree with backend, bot client, conventions."""

    def __init__(self) -> None:
        self.root = tempfile.mkdtemp(prefix="http_clients_")
        # Conventions config — opt-in for the http_clients block.
        _write(
            os.path.join(self.root, ".vc-context", "conventions.json"),
            """\
            {
              "rules": [],
              "http_clients": [
                {
                  "factory": "myproj.api_client.get_client",
                  "methods": ["post", "get", "patch", "delete"]
                }
              ]
            }
            """,
        )
        # FastAPI route — the destination.
        _write(
            os.path.join(self.root, "backend", "routes.py"),
            """\
            from fastapi import APIRouter
            router = APIRouter()

            @router.post("/api/admin/staff/admins")
            async def add_admin_route(): ...

            @router.delete("/api/admin/staff/admins/{user_id}")
            async def delete_admin_route(user_id: int): ...
            """,
        )
        # The factory module — its content doesn't matter for the AST
        # walk; we only key off the import path.
        _write(
            os.path.join(self.root, "myproj", "api_client", "__init__.py"),
            """\
            def get_client(): ...
            """,
        )
        # Inline call site.
        _write(
            os.path.join(self.root, "myproj", "bot", "staff.py"),
            """\
            from myproj.api_client import get_client

            async def add_admin(user_id, role="manager"):
                await get_client().post(
                    "/api/admin/staff/admins",
                    json={"user_id": user_id, "role": role},
                )

            async def delete_admin(user_id):
                await get_client().delete(f"/api/admin/staff/admins/{user_id}")
            """,
        )
        # Variable-bound call site.
        _write(
            os.path.join(self.root, "myproj", "bot", "other.py"),
            """\
            from myproj.api_client import get_client as _gc

            async def whatever():
                client = _gc()
                await client.post("/api/admin/staff/admins", json={})
            """,
        )

    def cleanup(self) -> None:
        for cur, dirs, files in os.walk(self.root, topdown=False):
            for f in files:
                os.remove(os.path.join(cur, f))
            for d in dirs:
                os.rmdir(os.path.join(cur, d))
        os.rmdir(self.root)


class TestLoadHttpClients(unittest.TestCase):
    def test_loads_factory_module_and_name(self) -> None:
        fx = _Fixture()
        try:
            specs = load_http_clients(fx.root)
            self.assertEqual(len(specs), 1)
            self.assertEqual(specs[0].factory_module, "myproj.api_client")
            self.assertEqual(specs[0].factory_name, "get_client")
            self.assertEqual(specs[0].methods, {"post", "get", "patch", "delete"})
            self.assertTrue(specs[0].first_arg_is_path)
        finally:
            fx.cleanup()

    def test_missing_block_returns_empty(self) -> None:
        tmpdir = tempfile.mkdtemp(prefix="http_no_cfg_")
        try:
            self.assertEqual(load_http_clients(tmpdir), [])
            _write(
                os.path.join(tmpdir, ".vc-context", "conventions.json"),
                '{"rules": []}',
            )
            self.assertEqual(load_http_clients(tmpdir), [])
        finally:
            for cur, dirs, files in os.walk(tmpdir, topdown=False):
                for f in files:
                    os.remove(os.path.join(cur, f))
                for d in dirs:
                    os.rmdir(os.path.join(cur, d))
            os.rmdir(tmpdir)

    def test_factory_must_be_dotted(self) -> None:
        tmpdir = tempfile.mkdtemp(prefix="http_bad_cfg_")
        try:
            _write(
                os.path.join(tmpdir, ".vc-context", "conventions.json"),
                '{"http_clients": [{"factory": "no_dots", "methods": ["post"]}]}',
            )
            self.assertEqual(load_http_clients(tmpdir), [])
        finally:
            for cur, dirs, files in os.walk(tmpdir, topdown=False):
                for f in files:
                    os.remove(os.path.join(cur, f))
                for d in dirs:
                    os.rmdir(os.path.join(cur, d))
            os.rmdir(tmpdir)


class TestCollectPythonCalls(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()
        self.specs = load_http_clients(self.fx.root)

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_inline_call_detected(self) -> None:
        calls = collect_python_calls(self.fx.root, self.specs)
        paths = {(c["path"], c["verb"], c["function"]) for c in calls}
        self.assertIn(
            ("/api/admin/staff/admins", "post", "add_admin"),
            paths,
        )

    def test_variable_bound_call_detected(self) -> None:
        calls = collect_python_calls(self.fx.root, self.specs)
        # The aliased ``_gc()`` call assigned to ``client`` should be
        # tracked through the local variable.
        functions = {c["function"] for c in calls}
        self.assertIn("whatever", functions)

    def test_fstring_path_skipped(self) -> None:
        # delete_admin uses ``f"/api/.../{user_id}"`` — that's not a
        # plain Constant, so the collector intentionally skips it.
        calls = collect_python_calls(self.fx.root, self.specs)
        self.assertFalse(
            any(c["function"] == "delete_admin" for c in calls),
            "f-strings should be ignored until f-string normalisation lands",
        )

    def test_no_specs_means_no_calls(self) -> None:
        self.assertEqual(collect_python_calls(self.fx.root, []), [])


class TestAttachAndQuery(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _Fixture()
        # Build the full route index (which already augments with
        # python callers) and write it where QueryEngine looks.
        index = build_route_index(self.fx.root)
        write_route_index(self.fx.root, index)
        # QueryEngine also reads agent_root.json on some queries; for
        # route_callers we don't strictly need it, so skip writing it.
        self.engine = QueryEngine(self.fx.root)
        self.index = index

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_route_index_has_python_callers(self) -> None:
        entry = self.index.get("/api/admin/staff/admins")
        self.assertIsNotNone(entry)
        py = entry.get("callers_python") or []
        files = sorted({c["file"] for c in py})
        self.assertIn("myproj/bot/staff.py", files)
        self.assertIn("myproj/bot/other.py", files)

    def test_route_callers_returns_unified_flat_list(self) -> None:
        flat = self.engine.route_callers("/api/admin/staff/admins")
        # Two python callers in the fixture, no JS.
        langs = {c["lang"] for c in flat}
        self.assertEqual(langs, {"python"})
        self.assertGreaterEqual(len(flat), 2)

    def test_attach_idempotent(self) -> None:
        # Running again must not duplicate records.
        index = build_route_index(self.fx.root)
        before = len(index["/api/admin/staff/admins"].get("callers_python") or [])
        # Re-attach — simulate a manual second pass.
        from indexers.http_callers import collect_python_calls as _collect

        attach_python_callers(index, _collect(self.fx.root, load_http_clients(self.fx.root)))
        after = len(index["/api/admin/staff/admins"].get("callers_python") or [])
        self.assertEqual(before, after)


class TestNoConfigBackwardCompat(unittest.TestCase):
    """Without an ``http_clients`` block the route index keeps its old
    shape — no ``callers_python`` field, no errors, no perf regression
    on the fast path."""

    def test_route_index_unchanged_without_config(self) -> None:
        tmpdir = tempfile.mkdtemp(prefix="http_no_block_")
        try:
            _write(
                os.path.join(tmpdir, "backend", "routes.py"),
                """\
                from fastapi import APIRouter
                router = APIRouter()

                @router.get("/api/foo")
                async def get_foo(): ...
                """,
            )
            index = build_route_index(tmpdir)
            entry = index.get("/api/foo")
            self.assertIsNotNone(entry)
            self.assertNotIn("callers_python", entry)
        finally:
            for cur, dirs, files in os.walk(tmpdir, topdown=False):
                for f in files:
                    os.remove(os.path.join(cur, f))
                for d in dirs:
                    os.rmdir(os.path.join(cur, d))
            os.rmdir(tmpdir)


if __name__ == "__main__":
    unittest.main()
