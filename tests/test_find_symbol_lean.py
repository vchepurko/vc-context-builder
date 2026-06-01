"""Tests for the find_symbol token-economy improvements:

* ``fields=`` whitelist — slim response.
* ``find_symbols`` — batch wrapper.
* ``include_body`` — internal-only; removed from the public MCP spec.
  Agents must use find_symbol (location) + read_slice (targeted range).
  Tests kept to guard the internal implementation used by conventions.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from query_engine import QueryEngine


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class _FixtureMixin:
    def _make_root(self) -> str:
        tmp = tempfile.mkdtemp(prefix="vc-find-")
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
                    },
                    "MyService": {
                        "file": "pkg/service.py",
                        "line": 1,
                        "end_line": 2,
                        "kind": "class",
                        "role": "service",
                    },
                    "FooComponent": {
                        "file": "pkg/foo.component.ts",
                        "line": 4,
                        "kind": "class",
                        "role": "ng-component",
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
        # Synthetic source files used by include_body.
        _write(
            os.path.join(tmp, "pkg/handlers.py"),
            (
                "import logging\n"
                "log = logging.getLogger(__name__)\n"
                "\n"
                "async def my_webhook(request):\n"
                '    """Handle a webhook callback."""\n'
                "    return 200\n"
            ),
        )
        _write(
            os.path.join(tmp, "pkg/service.py"), ("class MyService:\n    def run(self): return 1\n")
        )
        _write(
            os.path.join(tmp, "pkg/foo.component.ts"),
            (
                "import { Component } from '@angular/core';\n"
                "\n"
                "@Component({selector: 'app-foo'})\n"
                "export class FooComponent {\n"
                "  ngOnInit() {}\n"
                "}\n"
            ),
        )
        return tmp


class FieldsWhitelistTests(_FixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_root()
        self.engine = QueryEngine(self.root)

    def test_default_returns_full_record_with_test(self) -> None:
        out = self.engine.find_symbol("my_webhook")
        self.assertEqual(
            set(out.keys()), {"file", "line", "end_line", "kind", "params", "doc", "role", "test"}
        )

    def test_fields_file_and_line(self) -> None:
        # The "where + jump" case — file + 1-indexed start line, ~40 toks.
        out = self.engine.find_symbol("my_webhook", fields=["file", "line"])
        self.assertEqual(out, {"file": "pkg/handlers.py", "line": 4})

    def test_fields_file_only(self) -> None:
        out = self.engine.find_symbol("my_webhook", fields=["file"])
        self.assertEqual(out, {"file": "pkg/handlers.py"})

    def test_fields_pair(self) -> None:
        out = self.engine.find_symbol("my_webhook", fields=["file", "kind"])
        self.assertEqual(out, {"file": "pkg/handlers.py", "kind": "async-func"})

    def test_fields_drops_unknown_keys_silently(self) -> None:
        # 'gibberish' isn't a key — empty result, no crash.
        out = self.engine.find_symbol("my_webhook", fields=["gibberish"])
        self.assertEqual(out, {})

    def test_unknown_symbol_still_returns_none(self) -> None:
        self.assertIsNone(
            self.engine.find_symbol("nope", fields=["file"]),
        )


class IncludeBodyInternalTests(_FixtureMixin, unittest.TestCase):
    """Internal implementation tests — include_body is NOT exposed via MCP spec.
    The find_symbol + read_slice two-step pattern is the correct agent pattern."""

    def setUp(self) -> None:
        self.root = self._make_root()
        self.engine = QueryEngine(self.root)

    def test_python_function_body_via_ast(self) -> None:
        out = self.engine.find_symbol("my_webhook", include_body=True)
        body = out["body"]
        # AST get_source_segment returns the verbatim def block.
        self.assertIn("async def my_webhook(request):", body)
        self.assertIn("return 200", body)
        # Should NOT include the file's preamble (imports / log =).
        self.assertNotIn("import logging", body)

    def test_python_class_body_via_ast(self) -> None:
        out = self.engine.find_symbol("MyService", include_body=True)
        self.assertIn("class MyService:", out["body"])

    def test_typescript_class_body_via_regex(self) -> None:
        out = self.engine.find_symbol("FooComponent", include_body=True)
        body = out["body"]
        self.assertIn("export class FooComponent", body)
        # Cap respects line count even if file is shorter.
        self.assertLess(len(body.splitlines()), QueryEngine.BODY_SNIPPET_LINES + 1)

    def test_body_omitted_when_file_missing(self) -> None:
        # Drop the source file → record stays, body is just absent.
        os.remove(os.path.join(self.root, "pkg/handlers.py"))
        out = self.engine.find_symbol("my_webhook", include_body=True)
        self.assertNotIn("body", out)

    def test_body_combined_with_fields_whitelist(self) -> None:
        out = self.engine.find_symbol(
            "my_webhook",
            fields=["file", "body"],
            include_body=True,
        )
        self.assertEqual(set(out.keys()), {"file", "body"})


class FindSymbolsBatchTests(_FixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_root()
        self.engine = QueryEngine(self.root)

    def test_three_lookups_in_one_call(self) -> None:
        out = self.engine.find_symbols(["my_webhook", "MyService", "ghost"])
        self.assertEqual(set(out.keys()), {"my_webhook", "MyService", "ghost"})
        self.assertIsNotNone(out["my_webhook"])
        self.assertIsNotNone(out["MyService"])
        self.assertIsNone(out["ghost"])

    def test_batch_threads_fields_through(self) -> None:
        out = self.engine.find_symbols(
            ["my_webhook", "MyService"],
            fields=["file"],
        )
        self.assertEqual(out["my_webhook"], {"file": "pkg/handlers.py"})
        self.assertEqual(out["MyService"], {"file": "pkg/service.py"})

    def test_empty_list_returns_empty_dict(self) -> None:
        self.assertEqual(self.engine.find_symbols([]), {})


if __name__ == "__main__":
    unittest.main()
