"""Tests for the legacy regex-based FileParser fallback.

This is the simpler counterpart to the AST-based PythonParser /
TsJsParser — used as a last-resort scan when no specific parser is
registered. Tests cover the four extension branches plus the read
failure path so the parser stays a safe default.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from file_parser import FileParser


class _TmpFileMixin:
    def _write(self, name: str, content: str) -> str:
        tmp = tempfile.mkdtemp(prefix="vc-fp-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path


class PythonBranchTests(_TmpFileMixin, unittest.TestCase):
    def test_class_and_function_exports(self) -> None:
        path = self._write(
            "x.py",
            "class Foo: pass\ndef bar(): pass\nasync def baz(): pass\n",
        )
        out = FileParser.parse(path, ".py")
        self.assertEqual(out["exports"], ["Foo", "bar", "baz"])

    def test_import_dependencies(self) -> None:
        path = self._write(
            "x.py",
            "import os\nfrom typing import Dict\nfrom .local import x\n",
        )
        out = FileParser.parse(path, ".py")
        self.assertIn("os", out["dependencies"])
        self.assertIn("typing", out["dependencies"])


class PhpBranchTests(_TmpFileMixin, unittest.TestCase):
    def test_classes_and_hooks(self) -> None:
        path = self._write(
            "x.php",
            "<?php\nclass MyClass {}\nfunction my_fn() {}\n"
            "use App\\Service;\n"
            "add_action('init', 'my_fn');\n",
        )
        out = FileParser.parse(path, ".php")
        self.assertIn("MyClass", out["exports"])
        self.assertIn("my_fn", out["exports"])
        self.assertIn("App\\Service", out["dependencies"])
        self.assertIn("init", out["dependencies"])  # hook name


class JsTsBranchTests(_TmpFileMixin, unittest.TestCase):
    def test_export_forms(self) -> None:
        path = self._write(
            "x.ts",
            "export class Foo {}\n"
            "export const bar = 1;\n"
            "export default function baz() {}\n"
            "import { z } from './local';\n",
        )
        out = FileParser.parse(path, ".ts")
        self.assertIn("Foo", out["exports"])
        self.assertIn("bar", out["exports"])
        self.assertIn("baz", out["exports"])
        self.assertIn("./local", out["dependencies"])


class FailureModeTests(unittest.TestCase):
    def test_missing_file_returns_empty_result(self) -> None:
        # No exception is raised — returns the default {exports:[], deps:[]}.
        out = FileParser.parse("/nonexistent/path.py", ".py")
        self.assertEqual(out, {"exports": [], "dependencies": []})

    def test_unknown_extension_returns_empty(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".rs", delete=False) as fh:
            fh.write("fn main() {}\n")
            path = fh.name
        try:
            out = FileParser.parse(path, ".rs")
            # Rust isn't handled — returns the empty default.
            self.assertEqual(out, {"exports": [], "dependencies": []})
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
