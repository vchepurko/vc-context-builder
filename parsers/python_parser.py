import ast
from typing import Dict, List

from parsers.base_parser import BaseParser


class PythonParser(BaseParser):
    """AST-based parser for Python files.

    Per-file output:
      exports:      [{name, kind, params?, doc?}]   ← top-level only,
                       no _-private, with signature + docstring summary
      dependencies: [str]   ← top-level package names (filtering of
                       stdlib / third-party happens in agent_map.py)
    """

    extensions = ['.py']

    def extract(self, file_path: str) -> Dict[str, List]:
        content = self._read_file(file_path)
        if not content:
            return {"exports": [], "dependencies": []}

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return {"exports": [], "dependencies": []}

        exports: List[Dict[str, str]] = []
        deps: set = set()
        seen: set = set()

        # Top-level nodes only — nested defs are usually noise for an agent
        # trying to find a public surface to call.
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                if name.startswith("_") and not name.startswith("__"):
                    continue  # private helper, skip
                if name in seen:
                    continue
                seen.add(name)
                exports.append(self._summarise(node))

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    deps.add(alias.name.split(".")[0])

            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    deps.add(node.module.split(".")[0])

        return {"exports": exports, "dependencies": sorted(deps)}

    @staticmethod
    def _summarise(node) -> Dict[str, str]:
        kind = (
            "class" if isinstance(node, ast.ClassDef)
            else "async-func" if isinstance(node, ast.AsyncFunctionDef)
            else "func"
        )
        out: Dict[str, str] = {"name": node.name, "kind": kind}

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                out["params"] = "(" + ast.unparse(node.args) + ")"
            except Exception:
                out["params"] = "(...)"

        doc = ast.get_docstring(node) or ""
        for line in doc.splitlines():
            line = line.strip()
            if line:
                out["doc"] = line[:120]
                break

        return out
