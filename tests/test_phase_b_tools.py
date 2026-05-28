"""Tests for Tier-2 / Phase-B MCP tools.

Coverage:

* ``get_symbol_card`` — bundle shape + caps + missing-symbol case.
* ``get_file_card`` — slim export view + missing-file case.
* ``repo_map`` — module aggregation.
* ``get_changed_symbols`` — git diff parsing + line-overlap match
  (uses a real ``git init`` + commit to keep the test self-contained).
* ``get_decorated_with`` — name & suffix matching.
* ``HIDE_BY_DEFAULT`` extended to drop ``decorators`` too.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from indexers.impact_graph import build_impact_graph
from query_engine import QueryEngine


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class _FixtureMixin:
    def _make_root(self) -> str:
        tmp = tempfile.mkdtemp(prefix="vc-pb-")
        self.addCleanup(shutil.rmtree, tmp, True)

        _write(
            os.path.join(tmp, "agent_root.json"),
            json.dumps(
                {
                    "project_root": tmp,
                    "modules": ["./pkg"],
                    "roles": {"webhook": ["my_webhook"]},
                }
            ),
        )
        _write(
            os.path.join(tmp, "agent_symbols.json"),
            json.dumps(
                {
                    "my_webhook": {
                        "file": "pkg/handlers.py",
                        "line": 4,
                        "end_line": 6,
                        "kind": "async-func",
                        "params": "(request)",
                        "doc": "Handle a webhook callback.",
                        "role": "webhook",
                        "callees": ["log", "validate"],
                        "raises": ["HTTPError"],
                        "decorators": ["app.post", "cached"],
                    },
                    "MyService": {
                        "file": "pkg/service.py",
                        "line": 1,
                        "end_line": 3,
                        "kind": "class",
                        "decorators": ["dataclass"],
                    },
                }
            ),
        )
        _write(
            os.path.join(tmp, "agent_tests.json"),
            json.dumps(
                {
                    "my_webhook": {
                        "test_file": "tests/test_handlers.py",
                        "test_function": "test_my_webhook",
                        "line": 12,
                    },
                }
            ),
        )
        _write(
            os.path.join(tmp, "agent_impact.json"),
            json.dumps(
                {
                    "version": 1,
                    "symbols": {
                        "my_webhook": {
                            "file": "pkg/handlers.py",
                            "line": 4,
                            "direct": ["MyService"],
                            "test": {
                                "test_file": "tests/test_handlers.py",
                                "test_function": "test_my_webhook",
                                "line": 12,
                            },
                            "template_refs": [],
                        },
                        "MyService": {
                            "file": "pkg/service.py",
                            "line": 1,
                            "direct": [],
                            "test": {
                                "test_file": "tests/test_service.py",
                                "test_function": "test_service",
                                "line": 8,
                            },
                            "template_refs": [],
                        },
                    },
                }
            ),
        )
        # _module_map.json for the package — get_file_card needs it.
        _write(
            os.path.join(tmp, "pkg", "_module_map.json"),
            json.dumps(
                {
                    "directory": "./pkg",
                    "files": {
                        "handlers.py": {
                            "exports": [
                                {
                                    "name": "my_webhook",
                                    "kind": "async-func",
                                    "role": "webhook",
                                    "line": 4,
                                    "end_line": 6,
                                    "doc": "Handle a webhook callback.",
                                },
                            ],
                            "dependencies": ["fastapi"],
                        },
                        "service.py": {
                            "exports": [
                                {"name": "MyService", "kind": "class", "line": 1, "end_line": 3},
                            ],
                            "dependencies": [],
                        },
                    },
                }
            ),
        )
        return tmp


class GetSymbolCardTests(_FixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_root()
        self.engine = QueryEngine(self.root)

    def test_bundle_shape(self) -> None:
        card = self.engine.get_symbol_card("my_webhook")
        # Mandatory keys for shape stability.
        for key in ("name", "file", "line", "end_line", "kind", "callees", "raises", "callers"):
            self.assertIn(key, card, f"missing key: {key}")
        self.assertEqual(card["callees"], ["log", "validate"])
        self.assertEqual(card["raises"], ["HTTPError"])
        self.assertEqual(card["test"]["test_file"], "tests/test_handlers.py")
        self.assertEqual(card["callers"]["total"], 0)  # no deps in fixture

    def test_unknown_symbol(self) -> None:
        self.assertIsNone(self.engine.get_symbol_card("ghost"))

    def test_drops_optional_empty_keys(self) -> None:
        # MyService has no params, no doc, no role, no callees, no raises.
        card = self.engine.get_symbol_card("MyService")
        self.assertNotIn("params", card)
        self.assertNotIn("doc", card)
        self.assertNotIn("role", card)
        # ...but the "shape contract" keys stay even when empty.
        self.assertEqual(card["callees"], [])
        self.assertEqual(card["raises"], [])


class GetFileCardTests(_FixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_root()
        self.engine = QueryEngine(self.root)

    def test_simple_path(self) -> None:
        card = self.engine.get_file_card("pkg/handlers.py")
        self.assertEqual(card["file"], "pkg/handlers.py")
        self.assertEqual(len(card["exports"]), 1)
        self.assertEqual(card["exports"][0]["name"], "my_webhook")
        self.assertEqual(card["exports"][0]["line"], 4)
        self.assertEqual(card["roles"], {"webhook": 1})
        self.assertEqual(card["dependencies"], ["fastapi"])

    def test_missing_file(self) -> None:
        self.assertIsNone(self.engine.get_file_card("pkg/no_such.py"))


class RepoMapTests(_FixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_root()
        self.engine = QueryEngine(self.root)

    def test_aggregates_modules(self) -> None:
        out = self.engine.repo_map()
        self.assertEqual(len(out["modules"]), 1)
        m = out["modules"][0]
        self.assertEqual(m["files"], 2)
        self.assertEqual(m["exports"], 2)
        self.assertEqual(m["dominant_role"], "webhook")
        self.assertEqual(out["totals"]["files"], 2)
        self.assertEqual(out["totals"]["exports"], 2)


class ImpactTests(_FixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_root()
        self.engine = QueryEngine(self.root)

    def test_impact_reads_direct_and_tests_at_risk(self) -> None:
        out = self.engine.impact("my_webhook")
        self.assertEqual(out["symbol"], "my_webhook")
        self.assertEqual(out["direct"], ["MyService"])
        self.assertEqual(out["indirect"], [])
        self.assertEqual(out["tests_at_risk"][0]["symbol"], "MyService")
        self.assertEqual(out["tests_at_risk"][0]["test_file"], "tests/test_service.py")
        self.assertEqual(out["template_refs"], [])

    def test_unknown_symbol_returns_none(self) -> None:
        self.assertIsNone(self.engine.impact("ghost"))

    def test_build_impact_graph_uses_callees_and_module_dependencies(self) -> None:
        symbols = {
            "helper": {"file": "pkg/utils.py", "kind": "func"},
            "handler": {
                "file": "pkg/handlers.py",
                "kind": "func",
                "callees": ["helper"],
            },
            "route": {"file": "pkg/routes.py", "kind": "func"},
        }
        _write(
            os.path.join(self.root, "pkg", "_module_map.json"),
            json.dumps(
                {
                    "directory": "./pkg",
                    "files": {
                        "routes.py": {
                            "exports": [{"name": "route", "kind": "func"}],
                            "dependencies": ["handler"],
                        },
                    },
                }
            ),
        )
        graph = build_impact_graph(self.root, symbols, {})
        self.assertEqual(graph["symbols"]["helper"]["direct"], ["handler"])
        self.assertEqual(graph["symbols"]["handler"]["direct"], ["route"])


class GetDecoratedWithTests(_FixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_root()
        self.engine = QueryEngine(self.root)

    def test_exact_name_match(self) -> None:
        out = self.engine.get_decorated_with("cached")
        self.assertEqual([r["name"] for r in out], ["my_webhook"])

    def test_suffix_match(self) -> None:
        # `app.post` → bare suffix `post` should match it.
        out = self.engine.get_decorated_with("post")
        self.assertEqual([r["name"] for r in out], ["my_webhook"])

    def test_full_path_match(self) -> None:
        out = self.engine.get_decorated_with("app.post")
        self.assertEqual([r["name"] for r in out], ["my_webhook"])

    def test_dataclass(self) -> None:
        out = self.engine.get_decorated_with("dataclass")
        self.assertEqual([r["name"] for r in out], ["MyService"])

    def test_no_match(self) -> None:
        self.assertEqual(self.engine.get_decorated_with("nonexistent"), [])

    def test_empty_arg(self) -> None:
        self.assertEqual(self.engine.get_decorated_with(""), [])


class HideByDefaultTests(_FixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_root()
        self.engine = QueryEngine(self.root)

    def test_decorators_hidden_by_default(self) -> None:
        out = self.engine.find_symbol("my_webhook")
        self.assertNotIn("decorators", out)
        self.assertNotIn("callees", out)
        self.assertNotIn("raises", out)

    def test_decorators_visible_via_fields(self) -> None:
        out = self.engine.find_symbol("my_webhook", fields=["decorators"])
        self.assertEqual(out, {"decorators": ["app.post", "cached"]})


class GetChangedSymbolsTests(unittest.TestCase):
    """Wire a tiny git repo and verify hunk → symbol overlap."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-changed-")
        self.addCleanup(shutil.rmtree, self.root, True)

        # Init a real git repo so `git diff` works.
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=self.root,
            check=True,
        )
        # Initial commit — file with 3 funcs at known line ranges.
        _write(
            os.path.join(self.root, "pkg/work.py"),
            (
                "def alpha():\n"  # line 1
                "    return 1\n"  # line 2
                "\n"
                "def beta():\n"  # line 4
                "    return 2\n"  # line 5
                "\n"
                "def gamma():\n"  # line 7
                "    return 3\n"  # line 8
            ),
        )
        _write(
            os.path.join(self.root, "agent_root.json"),
            json.dumps(
                {
                    "project_root": self.root,
                    "modules": ["./pkg"],
                    "roles": {},
                }
            ),
        )
        _write(
            os.path.join(self.root, "agent_symbols.json"),
            json.dumps(
                {
                    "alpha": {"file": "pkg/work.py", "line": 1, "end_line": 2, "kind": "func"},
                    "beta": {"file": "pkg/work.py", "line": 4, "end_line": 5, "kind": "func"},
                    "gamma": {"file": "pkg/work.py", "line": 7, "end_line": 8, "kind": "func"},
                }
            ),
        )
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "init"],
            cwd=self.root,
            check=True,
        )

        # Edit beta only — should report only beta.
        _write(
            os.path.join(self.root, "pkg/work.py"),
            (
                "def alpha():\n"
                "    return 1\n"
                "\n"
                "def beta():\n"
                "    return 99\n"  # ← changed
                "\n"
                "def gamma():\n"
                "    return 3\n"
            ),
        )

        self.engine = QueryEngine(self.root)

    def test_only_beta_reported(self) -> None:
        out = self.engine.get_changed_symbols()
        names = [r["name"] for r in out]
        self.assertEqual(names, ["beta"])

    def test_no_diff_returns_empty(self) -> None:
        # Reset the working tree so diff is empty.
        subprocess.run(
            ["git", "checkout", "--", "pkg/work.py"],
            cwd=self.root,
            check=True,
        )
        self.assertEqual(self.engine.get_changed_symbols(), [])


if __name__ == "__main__":
    unittest.main()
