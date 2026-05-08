"""Unit tests for the cross-language route bridge (Feature C)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

import route_bridge
from query_engine import QueryEngine


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class CollectPythonRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-routes-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_get_route_extracted(self) -> None:
        _write(
            os.path.join(self.root, "backend", "routes", "x.py"),
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            '@router.get("/api/foo")\n'
            "async def find_foo():\n"
            "    return {}\n",
        )
        routes = route_bridge.collect_python_routes(self.root)
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["path"], "/api/foo")
        self.assertEqual(routes[0]["method"], "GET")
        self.assertEqual(routes[0]["handler"], "find_foo")

    def test_path_param_extracted_verbatim(self) -> None:
        _write(
            os.path.join(self.root, "r.py"),
            '@router.post("/api/users/{id}/promote")\ndef promote(id: int): pass\n',
        )
        routes = route_bridge.collect_python_routes(self.root)
        self.assertEqual(routes[0]["path"], "/api/users/{id}/promote")
        self.assertEqual(routes[0]["method"], "POST")

    def test_non_route_decorator_ignored(self) -> None:
        _write(os.path.join(self.root, "x.py"), '@some_other("not_a_route")\ndef f(): pass\n')
        self.assertEqual(route_bridge.collect_python_routes(self.root), [])

    def test_path_arg_not_string_skipped(self) -> None:
        _write(
            os.path.join(self.root, "x.py"), 'PATH = "/api/x"\n@router.get(PATH)\ndef f(): pass\n'
        )
        # We require the literal string in the decorator — variables
        # aren't resolved. Empty result is the right answer.
        self.assertEqual(route_bridge.collect_python_routes(self.root), [])


class CollectJsCallsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-routes-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_fetch_string_url(self) -> None:
        _write(
            os.path.join(self.root, "webapp", "api.js"), "fetch('/api/foo').then(r => r.json());\n"
        )
        calls = route_bridge.collect_js_calls(self.root)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["raw"], "/api/foo")
        self.assertEqual(calls[0]["prefix"], "/api/foo")
        self.assertEqual(calls[0]["verb"], "fetch")

    def test_template_literal_stripped(self) -> None:
        _write(os.path.join(self.root, "webapp", "api.js"), "fetch(`/api/products/${id}`);\n")
        calls = route_bridge.collect_js_calls(self.root)
        self.assertEqual(calls[0]["prefix"], "/api/products/*")

    def test_axios_get_post(self) -> None:
        _write(
            os.path.join(self.root, "x.ts"), "axios.get('/api/x');\nclient.post('/api/y', data);\n"
        )
        calls = route_bridge.collect_js_calls(self.root)
        verbs = sorted(c["verb"] for c in calls)
        self.assertEqual(verbs, ["get", "post"])

    def test_external_url_skipped(self) -> None:
        _write(os.path.join(self.root, "x.js"), "fetch('https://api.stripe.com/charges');\n")
        self.assertEqual(route_bridge.collect_js_calls(self.root), [])

    def test_query_string_dropped(self) -> None:
        _write(os.path.join(self.root, "x.js"), "fetch('/api/foo?bar=1&baz=2');\n")
        calls = route_bridge.collect_js_calls(self.root)
        self.assertEqual(calls[0]["prefix"], "/api/foo")


class MatchCallsToRoutesTests(unittest.TestCase):
    def test_static_path_matches(self) -> None:
        routes = [
            {"path": "/api/foo", "method": "GET", "handler": "find_foo", "file": "x.py", "line": 1}
        ]
        calls = [
            {"file": "a.js", "line": 1, "raw": "/api/foo", "prefix": "/api/foo", "verb": "fetch"}
        ]
        index = route_bridge.match_calls_to_routes(routes, calls)
        self.assertIn("/api/foo", index)
        self.assertEqual(len(index["/api/foo"]["callers_js"]), 1)

    def test_param_path_matches_template_call(self) -> None:
        routes = [
            {
                "path": "/api/products/{id}",
                "method": "GET",
                "handler": "get_product",
                "file": "x.py",
                "line": 1,
            }
        ]
        calls = [
            {
                "file": "a.js",
                "line": 5,
                "raw": "/api/products/${id}",
                "prefix": "/api/products/*",
                "verb": "get",
            }
        ]
        index = route_bridge.match_calls_to_routes(routes, calls)
        self.assertEqual(len(index["/api/products/{id}"]["callers_js"]), 1)

    def test_verb_mismatch_rejected(self) -> None:
        routes = [{"path": "/api/foo", "method": "GET", "handler": "h", "file": "x.py", "line": 1}]
        calls = [
            {"file": "a.js", "line": 1, "raw": "/api/foo", "prefix": "/api/foo", "verb": "post"}
        ]
        index = route_bridge.match_calls_to_routes(routes, calls)
        # GET-only route, POST call → no caller link.
        self.assertEqual(index["/api/foo"]["callers_js"], [])

    def test_caller_dedup(self) -> None:
        routes = [{"path": "/api/foo", "method": "GET", "handler": "h", "file": "x.py", "line": 1}]
        # Two identical call records at the same line/file.
        calls = [
            {"file": "a.js", "line": 1, "raw": "/api/foo", "prefix": "/api/foo", "verb": "get"},
            {"file": "a.js", "line": 1, "raw": "/api/foo", "prefix": "/api/foo", "verb": "fetch"},
        ]
        index = route_bridge.match_calls_to_routes(routes, calls)
        # First match wins per call; second one is verb-incompatible
        # only when route method differs — here GET allows both, so
        # both attempt insertion but dedup catches the duplicate.
        self.assertEqual(len(index["/api/foo"]["callers_js"]), 1)


class QueryEngineRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-routes-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _write_json(
            os.path.join(self.root, "agent_root.json"),
            {"project_root": self.root, "modules": ["."]},
        )
        _write_json(os.path.join(self.root, "agent_symbols.json"), {})
        _write_json(
            os.path.join(self.root, "agent_routes.json"),
            {
                "/api/foo": {
                    "method": "GET",
                    "handler": "find_foo",
                    "file": "backend/routes/x.py",
                    "line": 17,
                    "callers_js": [
                        {"file": "webapp/api.js", "line": 5, "raw": "/api/foo"},
                    ],
                },
            },
        )

    def test_find_route_hit(self) -> None:
        engine = QueryEngine(self.root)
        entry = engine.find_route("/api/foo")
        self.assertEqual(entry["handler"], "find_foo")
        self.assertEqual(entry["path"], "/api/foo")

    def test_find_route_miss(self) -> None:
        engine = QueryEngine(self.root)
        self.assertIsNone(engine.find_route("/api/ghost"))

    def test_route_callers_returns_list(self) -> None:
        engine = QueryEngine(self.root)
        callers = engine.route_callers("/api/foo")
        self.assertEqual(callers[0]["file"], "webapp/api.js")

    def test_route_for_js_call(self) -> None:
        engine = QueryEngine(self.root)
        hits = engine.route_for_js_call("webapp/api.js")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["path"], "/api/foo")

    def test_missing_routes_artifact_returns_empty(self) -> None:
        # Pure missing-file path — degrade gracefully.
        another_root = tempfile.mkdtemp(prefix="vc-no-routes-")
        self.addCleanup(shutil.rmtree, another_root, True)
        _write_json(
            os.path.join(another_root, "agent_root.json"),
            {"project_root": another_root, "modules": []},
        )
        _write_json(os.path.join(another_root, "agent_symbols.json"), {})
        engine = QueryEngine(another_root)
        self.assertEqual(engine.route_callers("/api/foo"), [])
        self.assertIsNone(engine.find_route("/api/foo"))


class CliRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-routes-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _write_json(
            os.path.join(self.root, "agent_root.json"),
            {"project_root": self.root, "modules": ["."]},
        )
        _write_json(os.path.join(self.root, "agent_symbols.json"), {})
        _write_json(
            os.path.join(self.root, "agent_routes.json"),
            {
                "/api/foo": {
                    "method": "GET",
                    "handler": "h",
                    "file": "x.py",
                    "line": 1,
                    "callers_js": [{"file": "a.js", "line": 1, "raw": "/api/foo"}],
                }
            },
        )

    def test_cli_route_hit(self) -> None:
        cli = os.path.join(_SUBMODULE, "cli.py")
        r = subprocess.run(
            [sys.executable, cli, "--root", self.root, "--json", "route", "/api/foo"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["handler"], "h")

    def test_cli_route_callers_hit(self) -> None:
        cli = os.path.join(_SUBMODULE, "cli.py")
        r = subprocess.run(
            [sys.executable, cli, "--root", self.root, "--json", "route-callers", "/api/foo"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload[0]["file"], "a.js")

    def test_cli_route_miss(self) -> None:
        cli = os.path.join(_SUBMODULE, "cli.py")
        r = subprocess.run(
            [sys.executable, cli, "--root", self.root, "route", "/no/such/route"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
