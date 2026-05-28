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

    def test_ng_component_selector_backfilled_for_non_standalone(self):
        """Reproduces the lms-client gap: ``standalone: false`` +
        ``templateUrl`` with extensive imports above the decorator
        used to push selector past the primary regex window. The
        backfill must recover it from the full file body.
        """
        long_imports = "\n".join([f"import {{ Lib{i} }} from '@scope/pkg{i}';" for i in range(80)])
        path = self._write(
            "profile.component.ts",
            f"""
{long_imports}

@Component({{
  selector: 'app-profile-edit-groups',
  templateUrl: './profile-edit-groups.component.html',
  styleUrls: ['./profile-edit-groups.component.scss'],
  standalone: false,
}})
export class ProfileEditGroupsComponent {{
  doSave() {{}}
}}
""",
        )
        result = self.parser.extract(path)
        comp = next(e for e in result["exports"] if e["name"] == "ProfileEditGroupsComponent")
        self.assertEqual(comp.get("role"), "ng-component")
        self.assertEqual(comp.get("ng_selector"), "app-profile-edit-groups")
        self.assertEqual(comp.get("ng_template_url"), "./profile-edit-groups.component.html")
        self.assertEqual(comp.get("ng_standalone"), False)

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

    # ------------------------------------------------------------------
    # end_line tests
    # ------------------------------------------------------------------

    def test_function_carries_end_line(self):
        path = self._write(
            "a.js",
            "export function foo() {\n  return 1;\n}\n",
        )
        result = self.parser.extract(path)
        exp = next(e for e in result["exports"] if e["name"] == "foo")
        self.assertEqual(exp["line"], 1)
        self.assertEqual(exp["end_line"], 3)

    def test_class_end_line_spans_full_class(self):
        src = (
            "export class MyService {\n"
            "  constructor() {}\n"
            "  doSomething() {\n"
            "    return 42;\n"
            "  }\n"
            "}\n"
        )
        path = self._write("svc.ts", src)
        result = self.parser.extract(path)
        exp = next(e for e in result["exports"] if e["name"] == "MyService")
        self.assertEqual(exp["line"], 1)
        self.assertEqual(exp["end_line"], 6)

    def test_const_arrow_end_line(self):
        src = "export const transform = (x) => {\n  return x * 2;\n};\n"
        path = self._write("util.ts", src)
        result = self.parser.extract(path)
        exp = next(e for e in result["exports"] if e["name"] == "transform")
        self.assertEqual(exp["line"], 1)
        self.assertGreaterEqual(exp["end_line"], exp["line"])

    def test_multiline_function_end_line_correct(self):
        src = (
            "// header\n"
            "export function process(\n"
            "  a: number,\n"
            "  b: number,\n"
            ") {\n"
            "  return a + b;\n"
            "}\n"
        )
        path = self._write("proc.ts", src)
        result = self.parser.extract(path)
        exp = next(e for e in result["exports"] if e["name"] == "process")
        self.assertEqual(exp["line"], 2)
        self.assertEqual(exp["end_line"], 7)

    def test_type_alias_end_line_on_same_or_next_line(self):
        src = "type Foo = string;\ntype Bar = number;\n"
        path = self._write("types.ts", src)
        result = self.parser.extract(path)
        by_name = {e["name"]: e for e in result["exports"]}
        self.assertIn("end_line", by_name["Foo"])
        self.assertLessEqual(by_name["Foo"]["end_line"], by_name["Bar"]["line"])


class EndLineForExtractBodyTests(unittest.TestCase):
    """Verify _query_symbols._extract_body uses end_line for JS/TS."""

    def setUp(self) -> None:
        import shutil
        import tempfile

        self.root = tempfile.mkdtemp(prefix="vc-endline-")
        self.src_content = (
            "export function add(a, b) {\n"
            "  return a + b;\n"
            "}\n"
            "\n"
            "export function subtract(a, b) {\n"
            "  return a - b;\n"
            "}\n"
        )
        js_path = os.path.join(self.root, "math.js")
        with open(js_path, "w") as fh:
            fh.write(self.src_content)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_extract_body_bounded_by_end_line(self):
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        from _query_symbols import _QuerySymbolsMixin

        class _Stub(_QuerySymbolsMixin):
            project_root = self.root
            BODY_SNIPPET_LINES = 200
            BODY_SNIPPET_MAX_BYTES = 8000
            SYMBOLS_FILENAME = ""

        stub = _Stub()
        record = {"file": "math.js", "line": 1, "end_line": 3}
        body = stub._extract_body("add", record)
        self.assertIsNotNone(body)
        assert body is not None
        self.assertIn("return a + b", body)
        # subtract should NOT appear — end_line=3 stops before line 5
        self.assertNotIn("subtract", body)


if __name__ == "__main__":
    unittest.main()
