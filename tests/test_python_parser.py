import os
import sys
import unittest

# Add the root directory to sys.path so tests can see the parsers module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from parsers.python_parser import PythonParser


class TestPythonASTParser(unittest.TestCase):
    def setUp(self):
        self.test_file = "dummy_ast_test.py"
        self.parser = PythonParser()

        # Create a complex dummy Python file for AST testing
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("""
import os
import sys as system_module
from typing import List, Dict

# class FakeClass:  <-- Regex would catch this, but AST should ignore it
#     pass

class DatabaseConnector:
    def connect(self):
        pass

def global_helper_function():
    pass

async def fetch_data_async():
    pass
            """)

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_extract_ast(self):
        result = self.parser.extract(self.test_file)

        # Verify Exports (Classes and functions) — exports are dicts
        # with at least a `name` key, so we project to names first.
        names = [e["name"] for e in result["exports"]]
        self.assertIn("DatabaseConnector", names)
        self.assertIn("global_helper_function", names)
        self.assertIn("fetch_data_async", names)
        self.assertNotIn("FakeClass", names, "AST parser should ignore comments!")

        # Verify Dependencies (Imports)
        self.assertIn("os", result["dependencies"])
        self.assertIn("sys", result["dependencies"])
        self.assertIn("typing", result["dependencies"])

    def test_line_numbers(self):
        """Each export carries 1-indexed `line` and `end_line` from the AST."""
        result = self.parser.extract(self.test_file)
        by_name = {e["name"]: e for e in result["exports"]}

        # The fixture starts with a blank line, so line numbers are
        # offset by 1 from the literal source. Anchor on the keyword
        # presence rather than hard-coded lines so the test survives
        # whitespace tweaks above.
        with open(self.test_file, encoding="utf-8") as fh:
            lines = fh.readlines()

        for name, kw in (
            ("DatabaseConnector", "class DatabaseConnector"),
            ("global_helper_function", "def global_helper_function"),
            ("fetch_data_async", "async def fetch_data_async"),
        ):
            entry = by_name[name]
            self.assertIn("line", entry, f"{name}: missing `line`")
            self.assertIn("end_line", entry, f"{name}: missing `end_line`")
            self.assertGreaterEqual(entry["end_line"], entry["line"])
            self.assertIn(kw, lines[entry["line"] - 1])


class MethodDecoratorsTest(unittest.TestCase):
    """Class-level `decorators` should fold in method-level ones too —
    so `get_decorated_with("staticmethod")` finds the enclosing class.
    """

    def setUp(self):
        self.test_file = "dummy_method_decorators_test.py"
        self.parser = PythonParser()
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write(
                "from abc import abstractmethod\n"
                "\n"
                "@dataclass\n"
                "class Foo:\n"
                "    @staticmethod\n"
                "    def helper(): pass\n"
                "    @property\n"
                "    def name(self): return 'x'\n"
                "    @abstractmethod\n"
                "    async def do(self): pass\n"
                "\n"
                "class Bare:\n"
                "    def m(self): pass\n"
            )

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_class_collects_method_decorators(self):
        result = self.parser.extract(self.test_file)
        by_name = {e["name"]: e for e in result["exports"]}
        decorators = set(by_name["Foo"].get("decorators") or [])
        # Class-level dataclass + each method's decorator merged.
        for expected in ("dataclass", "staticmethod", "property", "abstractmethod"):
            self.assertIn(expected, decorators, f"missing: {expected}")

    def test_class_without_decorators_emits_nothing(self):
        result = self.parser.extract(self.test_file)
        by_name = {e["name"]: e for e in result["exports"]}
        # `Bare` has no class decorator AND its method has no decorator
        # → `decorators` field should be absent (empty-list trim).
        self.assertNotIn("decorators", by_name["Bare"])


if __name__ == "__main__":
    unittest.main()
