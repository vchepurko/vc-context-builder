"""Tests for the configurable role detector (`custom_roles.py`).

Synthetic fixture project:

    fixture/
      .vc-context/roles.json    — Express + React rules
      app.js                    — Express route registration
      Button.jsx                — React component
      hooks.js                  — `useFoo` hook (would be react-hook
                                  via built-in, but we override)
      handlers.py               — Python file, gets one Python rule
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

# Ensure imports resolve when running via `python3 -m unittest …`.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from custom_roles import (
    CustomRole,
    apply_custom_roles,
    load_custom_roles,
    should_override_builtin,
)
from agent_map import ContextBuilder


# ---------------------------------------------------------------------------
# Pure-function tests (no fixture project)
# ---------------------------------------------------------------------------

class LoadCustomRolesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg_dir = os.path.join(self.tmp, ".vc-context")
        os.makedirs(self.cfg_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_config(self, payload):
        with open(os.path.join(self.cfg_dir, "roles.json"), "w") as fh:
            json.dump(payload, fh)

    def test_missing_config_returns_empty_list(self):
        self.assertEqual(load_custom_roles(self.tmp), [])

    def test_malformed_json_returns_empty_list(self):
        with open(os.path.join(self.cfg_dir, "roles.json"), "w") as fh:
            fh.write("{not json")
        self.assertEqual(load_custom_roles(self.tmp), [])

    def test_drops_rule_without_id(self):
        self._write_config({"roles": [{"match_path": "**/*.go"}]})
        self.assertEqual(load_custom_roles(self.tmp), [])

    def test_drops_rule_without_any_matcher(self):
        self._write_config({"roles": [{"id": "naked-rule"}]})
        self.assertEqual(load_custom_roles(self.tmp), [])

    def test_invalid_regex_treated_as_unset(self):
        # `(unclosed` is invalid; rule still loads with other matchers
        # but the bad pattern is dropped. We need at least one good
        # matcher to survive.
        self._write_config({"roles": [{
            "id": "broken",
            "match_decorator_or_call": "(unclosed",
            "match_path": "**/*.py",
        }]})
        rules = load_custom_roles(self.tmp)
        self.assertEqual(len(rules), 1)
        self.assertIsNone(rules[0].match_decorator_or_call)
        self.assertEqual(rules[0].match_path, "**/*.py")

    def test_rules_sorted_by_priority_desc(self):
        self._write_config({"roles": [
            {"id": "low", "match_path": "**/*", "priority": 1},
            {"id": "high", "match_path": "**/*", "priority": 10},
            {"id": "mid", "match_path": "**/*", "priority": 5},
        ]})
        rules = load_custom_roles(self.tmp)
        self.assertEqual([r.id for r in rules], ["high", "mid", "low"])


class ApplyCustomRolesTests(unittest.TestCase):
    def test_returns_none_when_no_rules(self):
        self.assertIsNone(apply_custom_roles({"name": "x", "kind": "func"},
                                              "x.py", "", []))

    def test_path_glob_with_braces(self):
        rule = CustomRole(id="js-or-ts", match_path="**/*.{js,ts}")
        self.assertEqual(
            apply_custom_roles({"name": "x", "kind": "func"},
                               "/abs/src/foo.ts", "", [rule],
                               project_root="/abs"),
            "js-or-ts",
        )
        self.assertEqual(
            apply_custom_roles({"name": "x", "kind": "func"},
                               "/abs/src/foo.js", "", [rule],
                               project_root="/abs"),
            "js-or-ts",
        )
        # `.tsx` shouldn't match the {js,ts} alternation.
        self.assertIsNone(apply_custom_roles(
            {"name": "x", "kind": "func"},
            "/abs/src/foo.tsx", "", [rule], project_root="/abs",
        ))

    def test_priority_resolves_conflict(self):
        import re as _re
        low = CustomRole(id="low", priority=1, match_function_name=_re.compile("^foo"))
        high = CustomRole(id="high", priority=9, match_function_name=_re.compile("^foo"))
        # Rules unsorted on purpose — apply_custom_roles must scan all
        # before returning.
        result = apply_custom_roles({"name": "foo_bar", "kind": "func"},
                                     "x.py", "", [low, high])
        self.assertEqual(result, "high")

    def test_should_override_builtin_priority_rule(self):
        self.assertTrue(should_override_builtin("custom", 5, None))
        self.assertTrue(should_override_builtin("custom", 5, "route"))
        self.assertFalse(should_override_builtin("custom", 0, "route"))
        self.assertFalse(should_override_builtin(None, 5, "route"))


# ---------------------------------------------------------------------------
# End-to-end tests via ContextBuilder
# ---------------------------------------------------------------------------

class BuilderWithCustomRolesTests(unittest.TestCase):
    """Spin up a synthetic project and run the full builder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._write_fixture()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_fixture(self):
        cfg_dir = os.path.join(self.tmp, ".vc-context")
        os.makedirs(cfg_dir)
        config = {
            "roles": [
                {
                    "id": "express-route",
                    "match_path": "**/*.{js,ts}",
                    "match_decorator_or_call": r"(app|router)\.(get|post|put|patch|delete)",
                    "priority": 10,
                },
                {
                    "id": "react-component",
                    "match_path": "**/*.{jsx,tsx}",
                    "match_function_returns": r"<[A-Z]",
                    "match_function_name": r"^[A-Z]",
                    "priority": 5,
                },
            ]
        }
        with open(os.path.join(cfg_dir, "roles.json"), "w") as fh:
            json.dump(config, fh)

        # Express-style file
        os.makedirs(os.path.join(self.tmp, "src"))
        with open(os.path.join(self.tmp, "src", "app.js"), "w") as fh:
            fh.write(
                "const express = require('express');\n"
                "const app = express();\n"
                "function handleHello(req, res) { return res.send('hi'); }\n"
                "app.get('/hello', handleHello);\n"
            )

        # React component file
        with open(os.path.join(self.tmp, "src", "Button.jsx"), "w") as fh:
            fh.write(
                "import React from 'react';\n"
                "function Button(props) {\n"
                "  return <button onClick={props.onClick}>X</button>;\n"
                "}\n"
                "export default Button;\n"
            )

    def _read_module_map(self, rel_dir):
        with open(os.path.join(self.tmp, rel_dir, "_module_map.json")) as fh:
            return json.load(fh)

    def test_express_route_tagged(self):
        ContextBuilder(self.tmp).run()
        m = self._read_module_map("src")
        exports = m["files"]["app.js"]["exports"]
        roles = {e["name"]: e.get("role") for e in exports}
        self.assertEqual(roles.get("handleHello"), "express-route")

    def test_react_component_tagged_via_custom_rule(self):
        ContextBuilder(self.tmp).run()
        m = self._read_module_map("src")
        exports = m["files"]["Button.jsx"]["exports"]
        button = next(e for e in exports if e["name"] == "Button")
        # Built-in tags it `react-component` too; the custom rule has
        # priority 5 — so the custom one wins (its id happens to match
        # the built-in name in this fixture).
        self.assertEqual(button.get("role"), "react-component")

    def test_no_config_file_means_no_custom_tags(self):
        # Wipe the config — built-in roles must still apply.
        os.remove(os.path.join(self.tmp, ".vc-context", "roles.json"))
        # Need to also wipe stale module maps from the prior run, since
        # _needs_update only kicks in on file mtime.
        for cur, _dirs, files in os.walk(self.tmp):
            for f in files:
                if f == "_module_map.json":
                    os.remove(os.path.join(cur, f))
        ContextBuilder(self.tmp).run()
        m = self._read_module_map("src")
        # `Button` still gets a role from the built-in detector.
        button = next(e for e in m["files"]["Button.jsx"]["exports"]
                      if e["name"] == "Button")
        self.assertEqual(button.get("role"), "react-component")
        # `handleHello` gets `express-route` from the built-in detector
        # (registration site).
        hello = next(e for e in m["files"]["app.js"]["exports"]
                      if e["name"] == "handleHello")
        self.assertEqual(hello.get("role"), "express-route")


if __name__ == "__main__":
    unittest.main()
