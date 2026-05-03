import ast
from typing import Dict, List
from parsers.base_parser import BaseParser

class PythonParser(BaseParser):
    """Deep AST-based parser for Python files."""

    extensions = ['.py']

    def extract(self, file_path: str) -> Dict[str, List[str]]:
        content = self._read_file(file_path)
        exports = set()
        dependencies = set()

        if not content:
            return {"exports": [], "dependencies": []}

        try:
            # Parse the code into an Abstract Syntax Tree
            tree = ast.parse(content)

            # Traverse every node in the tree
            for node in ast.walk(tree):
                # Extract classes, synchronous and asynchronous functions
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    exports.add(node.name)

                # Extract standard imports (e.g., import os)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        dependencies.add(alias.name)

                # Extract from-imports (e.g., from typing import List)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        dependencies.add(node.module)

        except SyntaxError:
            # If the file contains syntax errors, fail gracefully
            # and return empty lists rather than crashing the pipeline
            pass

        return {
            "exports": list(exports),
            "dependencies": list(dependencies)
        }