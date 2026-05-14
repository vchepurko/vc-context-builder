"""Tests for the upgraded JS/TS parser.

Covers:
- Top-level-only declarations (nested defs ignored).
- Signatures captured for both arrow and function-statement forms.
- JSDoc summaries (first non-empty line, @param/@returns blocks dropped,
  120-char cap).
- Built-in roles: react-component, react-hook, express-route,
  vue-composable.
- Imports normalised to their top-level package name.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from parsers.ts_js_parser import TsJsParser


class TsJsParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.parser = TsJsParser()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, content: str) -> str:
        path = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(name) else None
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    # ------------------------------------------------------------------
    # Top-level only
    # ------------------------------------------------------------------

    def test_typescript_interface_extracted_with_kind(self):
        """Closes the 57.9% empty-ratio blind spot on TS interface
        lookups observed in real lms-client sessions."""
        path = self._write(
            "shape.ts",
            """
export interface SectionState {
  id: number;
  name: string;
}

interface Internal<T> extends Base {
  payload: T;
}
""",
        )
        result = self.parser.extract(path)
        by_name = {e["name"]: e for e in result["exports"]}
        self.assertIn("SectionState", by_name)
        self.assertEqual(by_name["SectionState"]["kind"], "interface")
        self.assertIn("Internal", by_name)
        self.assertEqual(by_name["Internal"]["kind"], "interface")

    def test_typescript_type_alias_extracted(self):
        path = self._write(
            "alias.ts",
            """
export type ID = string | number;
type Pair<K, V> = { k: K; v: V };
""",
        )
        result = self.parser.extract(path)
        by_name = {e["name"]: e["kind"] for e in result["exports"]}
        self.assertEqual(by_name.get("ID"), "type")
        self.assertEqual(by_name.get("Pair"), "type")

    def test_typescript_kinds_carry_line_numbers(self):
        path = self._write(
            "lines.ts",
            "// preamble\n// preamble\nexport interface Foo { x: number }\ntype Bar = string;\n",
        )
        result = self.parser.extract(path)
        by_name = {e["name"]: e["line"] for e in result["exports"]}
        self.assertEqual(by_name["Foo"], 3)
        self.assertEqual(by_name["Bar"], 4)

    def test_top_level_only_skips_nested(self):
        path = self._write(
            "a.js",
            """
function outer() {
  function inner() { return 1; }
  return inner();
}
""",
        )
        result = self.parser.extract(path)
        names = [e["name"] for e in result["exports"]]
        self.assertIn("outer", names)
        self.assertNotIn("inner", names)

    # ------------------------------------------------------------------
    # Signatures
    # ------------------------------------------------------------------

    def test_function_statement_signature(self):
        path = self._write("a.js", "function add(a, b) { return a + b; }\n")
        result = self.parser.extract(path)
        add = next(e for e in result["exports"] if e["name"] == "add")
        self.assertEqual(add["params"], "(a, b)")
        self.assertEqual(add["kind"], "func")

    def test_arrow_signature(self):
        path = self._write("a.js", "const mul = (a, b) => a * b;\n")
        result = self.parser.extract(path)
        mul = next(e for e in result["exports"] if e["name"] == "mul")
        self.assertEqual(mul["params"], "(a, b)")

    def test_async_function_kind(self):
        path = self._write(
            "a.ts", "export async function fetchUser(id: number) {\n  return null;\n}\n"
        )
        result = self.parser.extract(path)
        fu = next(e for e in result["exports"] if e["name"] == "fetchUser")
        self.assertEqual(fu["kind"], "async-func")

    # ------------------------------------------------------------------
    # JSDoc
    # ------------------------------------------------------------------

    def test_jsdoc_first_line_used(self):
        path = self._write(
            "a.js",
            """
/**
 * Multiplies two numbers.
 * @param a
 * @param b
 */
function mul(a, b) { return a * b; }
""",
        )
        result = self.parser.extract(path)
        mul = next(e for e in result["exports"] if e["name"] == "mul")
        self.assertEqual(mul.get("doc"), "Multiplies two numbers.")

    def test_jsdoc_skipped_if_only_tags(self):
        path = self._write(
            "a.js",
            """
/**
 * @param a
 * @returns void
 */
function noop(a) {}
""",
        )
        result = self.parser.extract(path)
        noop = next(e for e in result["exports"] if e["name"] == "noop")
        self.assertNotIn("doc", noop)

    def test_jsdoc_capped_at_120_chars(self):
        long_line = "X" * 200
        path = self._write(
            "a.js",
            f"""
/**
 * {long_line}
 */
function huge() {{}}
""",
        )
        result = self.parser.extract(path)
        huge = next(e for e in result["exports"] if e["name"] == "huge")
        self.assertEqual(len(huge["doc"]), 120)

    # ------------------------------------------------------------------
    # Built-in roles
    # ------------------------------------------------------------------

    def test_react_component_role_in_jsx(self):
        path = self._write(
            "Button.jsx",
            """
import React from 'react';
function Button(props) {
  return <button>{props.label}</button>;
}
""",
        )
        result = self.parser.extract(path)
        btn = next(e for e in result["exports"] if e["name"] == "Button")
        self.assertEqual(btn.get("role"), "react-component")

    def test_react_component_only_jsx_tsx(self):
        # `.js` file with JSX-shaped name but no JSX file extension —
        # should NOT get tagged.
        path = self._write(
            "Button.js",
            """
function Button(props) {
  return <button>{props.label}</button>;
}
""",
        )
        result = self.parser.extract(path)
        btn = next(e for e in result["exports"] if e["name"] == "Button")
        self.assertNotEqual(btn.get("role"), "react-component")

    def test_react_hook_role(self):
        path = self._write(
            "hooks.ts",
            """
import { useState } from 'react';
function useCounter() {
  const [n, setN] = useState(0);
  return n;
}
""",
        )
        result = self.parser.extract(path)
        hook = next(e for e in result["exports"] if e["name"] == "useCounter")
        self.assertEqual(hook.get("role"), "react-hook")

    def test_express_route_role(self):
        path = self._write(
            "routes.js",
            """
const express = require('express');
const router = express.Router();
function listUsers(req, res) { res.json([]); }
router.get('/users', listUsers);
""",
        )
        result = self.parser.extract(path)
        lu = next(e for e in result["exports"] if e["name"] == "listUsers")
        self.assertEqual(lu.get("role"), "express-route")

    def test_express_route_app_form(self):
        path = self._write(
            "server.js",
            """
const app = express();
function rootHandler(req, res) { res.send('ok'); }
app.post('/api/foo', rootHandler);
""",
        )
        result = self.parser.extract(path)
        rh = next(e for e in result["exports"] if e["name"] == "rootHandler")
        self.assertEqual(rh.get("role"), "express-route")

    def test_vue_composable_role(self):
        path = self._write(
            "src/composables/useUser.ts",
            """
import { ref } from 'vue';
export function useUser() {
  return ref(null);
}
""",
        )
        result = self.parser.extract(path)
        uu = next(e for e in result["exports"] if e["name"] == "useUser")
        self.assertEqual(uu.get("role"), "vue-composable")

    def test_plain_function_no_role(self):
        path = self._write("util.js", "function plain(x) { return x + 1; }\n")
        result = self.parser.extract(path)
        plain = next(e for e in result["exports"] if e["name"] == "plain")
        self.assertNotIn("role", plain)

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def test_imports_top_level_pkg(self):
        path = self._write(
            "a.js",
            """
import React from 'react';
import { something } from 'lodash/fp';
import * as path from 'path';
const fs = require('fs/promises');
""",
        )
        result = self.parser.extract(path)
        deps = set(result["dependencies"])
        self.assertIn("react", deps)
        self.assertIn("lodash", deps)
        self.assertIn("path", deps)
        self.assertIn("fs", deps)

    def test_relative_imports_dropped(self):
        path = self._write(
            "a.js",
            """
import x from './local';
import y from '../sibling';
""",
        )
        result = self.parser.extract(path)
        self.assertEqual(result["dependencies"], [])

    def test_scoped_pkg_kept_as_scope(self):
        path = self._write("a.js", "import x from '@scope/pkg/sub';\n")
        result = self.parser.extract(path)
        # The Python parser uses the first segment after split('/'),
        # which for a scoped package is the `@scope` head. Documenting
        # the behaviour rather than asserting a specific shape.
        self.assertIn("@scope", result["dependencies"])


if __name__ == "__main__":
    unittest.main()
