"""Tests for the Tier-1 evidence tools:

* AST fact extraction in ``PythonParser._extract_facts`` (callees, raises).
* ``QueryEngine.get_callees`` / ``get_raised_exceptions`` projections.
* ``QueryEngine.read_slice`` — bounded source-read primitive.
* ``find_symbol`` default response hides fact fields.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from parsers.python_parser import PythonParser  # noqa: E402
from query_engine import QueryEngine  # noqa: E402


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class ExtractFactsTests(unittest.TestCase):
    """Direct tests against the AST-walk used during indexing."""

    def _facts(self, src: str) -> tuple:
        tree = ast.parse(src)
        node = tree.body[0]
        return PythonParser._extract_facts(node)

    def test_function_callees_simple(self) -> None:
        callees, _ = self._facts(
            "def f():\n    g()\n    h(1, 2)\n    obj.method()\n"
        )
        self.assertEqual(callees, {"g", "h", "method"})

    def test_function_raises(self) -> None:
        _, raises = self._facts(
            "def f():\n"
            "    if x: raise ValueError('bad')\n"
            "    raise httpx.HTTPError()\n"
        )
        self.assertEqual(raises, {"ValueError", "HTTPError"})

    def test_bare_raise_ignored(self) -> None:
        _, raises = self._facts(
            "def f():\n"
            "    try: ...\n"
            "    except Exception: raise\n"
        )
        self.assertEqual(raises, set())

    def test_class_walks_method_bodies(self) -> None:
        callees, raises = self._facts(
            "class C:\n"
            "    def m(self):\n"
            "        helper()\n"
            "        raise RuntimeError\n"
        )
        self.assertIn("helper", callees)
        self.assertIn("RuntimeError", raises)

    def test_empty_body_yields_empty_sets(self) -> None:
        callees, raises = self._facts("def f(): pass\n")
        self.assertEqual(callees, set())
        self.assertEqual(raises, set())


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-facts-")
        self.addCleanup(shutil.rmtree, self.root, True)

        _write(os.path.join(self.root, "agent_root.json"), json.dumps({
            "project_root": self.root,
            "modules": ["./pkg"],
            "roles": {},
        }))
        _write(os.path.join(self.root, "agent_symbols.json"), json.dumps({
            "do_work": {
                "file": "pkg/work.py",
                "line": 1,
                "end_line": 6,
                "kind": "func",
                "callees": ["fetch", "log_event"],
                "raises": ["ValueError"],
            },
            "DataClass": {
                "file": "pkg/data.py",
                "line": 1,
                "end_line": 3,
                "kind": "class",
            },
        }))
        _write(os.path.join(self.root, "pkg/work.py"), (
            "def do_work():\n"
            "    fetch()\n"
            "    log_event('start')\n"
            "    if bad():\n"
            "        raise ValueError('nope')\n"
            "    return 1\n"
        ))
        _write(os.path.join(self.root, "pkg/data.py"), (
            "class DataClass:\n"
            "    x = 1\n"
            "    y = 2\n"
        ))
        self.engine = QueryEngine(self.root)


class FactProjectionTests(_Fixture):
    def test_get_callees_returns_list(self) -> None:
        self.assertEqual(
            self.engine.get_callees("do_work"), ["fetch", "log_event"],
        )

    def test_get_callees_unknown_symbol(self) -> None:
        self.assertEqual(self.engine.get_callees("ghost"), [])

    def test_get_callees_symbol_without_calls(self) -> None:
        self.assertEqual(self.engine.get_callees("DataClass"), [])

    def test_get_raised_exceptions(self) -> None:
        self.assertEqual(
            self.engine.get_raised_exceptions("do_work"), ["ValueError"],
        )

    def test_get_raised_exceptions_unknown(self) -> None:
        self.assertEqual(self.engine.get_raised_exceptions("ghost"), [])


class FindSymbolHidesFactsTests(_Fixture):
    def test_default_response_omits_callees_and_raises(self) -> None:
        out = self.engine.find_symbol("do_work")
        self.assertNotIn("callees", out)
        self.assertNotIn("raises", out)
        # Other fields stay.
        self.assertIn("file", out)
        self.assertIn("line", out)

    def test_explicit_fields_brings_facts_back(self) -> None:
        out = self.engine.find_symbol(
            "do_work", fields=["file", "callees", "raises"],
        )
        self.assertEqual(
            out, {
                "file": "pkg/work.py",
                "callees": ["fetch", "log_event"],
                "raises": ["ValueError"],
            },
        )


class ReadSliceTests(_Fixture):
    def test_simple_range(self) -> None:
        out = self.engine.read_slice("pkg/work.py", 2, 3)
        self.assertEqual(out["start"], 2)
        self.assertEqual(out["end"], 3)
        self.assertEqual(
            out["content"],
            "    fetch()\n    log_event('start')",
        )
        self.assertFalse(out["truncated"])

    def test_path_traversal_rejected(self) -> None:
        # Even when the resolved path exists, escaping project_root must fail.
        outside = os.path.join(tempfile.gettempdir(), "elsewhere.py")
        with open(outside, "w") as fh:
            fh.write("secret = 1\n")
        self.addCleanup(os.remove, outside)
        rel = os.path.relpath(outside, self.root)
        self.assertIsNone(self.engine.read_slice(rel, 1, 1))

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(self.engine.read_slice("pkg/nope.py", 1, 1))

    def test_invalid_range(self) -> None:
        self.assertIsNone(self.engine.read_slice("pkg/work.py", 0, 1))
        self.assertIsNone(self.engine.read_slice("pkg/work.py", 5, 2))

    def test_line_cap_truncates(self) -> None:
        big = "\n".join(f"line {i}" for i in range(1, 500)) + "\n"
        _write(os.path.join(self.root, "pkg/big.py"), big)
        # Request 1..400, but cap is SLICE_MAX_LINES (200).
        out = self.engine.read_slice("pkg/big.py", 1, 400)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["start"], 1)
        self.assertEqual(out["end"], QueryEngine.SLICE_MAX_LINES)
        self.assertEqual(
            len(out["content"].splitlines()), QueryEngine.SLICE_MAX_LINES,
        )


if __name__ == "__main__":
    unittest.main()
