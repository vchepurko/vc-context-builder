import re
from typing import Dict, List
from parsers.base_parser import BaseParser

class TsJsParser(BaseParser):
    """
    Advanced heuristic parser for TypeScript and JavaScript.
    Strips comments before parsing to mimic AST-level precision.
    """

    # Extended to support React/Next.js files
    extensions = ['.js', '.ts', '.jsx', '.tsx']

    def extract(self, file_path: str) -> Dict[str, List[str]]:
        content = self._read_file(file_path)

        if not content:
            return {"exports": [], "dependencies": []}

        # 1. Pre-processing: Remove comments to avoid false positives
        # Strip block comments (/* ... */)
        content = re.sub(r'/\*[\s\S]*?\*/', '', content)
        # Strip inline comments (// ...)
        content = re.sub(r'//.*', '', content)

        exports = []
        dependencies = []

        # 2. Extract Exports (handles async, default, abstract, and standard types)
        export_pattern = r'export\s+(?:default\s+)?(?:async\s+)?(?:abstract\s+)?(?:class|function|const|let|var|interface|type)\s+([a-zA-Z0-9_]+)'
        exports.extend(re.findall(export_pattern, content))

        # 3. Extract Static Imports
        import_pattern = r'import\s+.*?from\s+[\'"]([a-zA-Z0-9_/\.-]+)[\'"]'
        dependencies.extend(re.findall(import_pattern, content))

        # 4. Extract Dynamic Imports and Requires
        dynamic_import_pattern = r'(?:require|import)\s*\(\s*[\'"]([a-zA-Z0-9_/\.-]+)[\'"]\s*\)'
        dependencies.extend(re.findall(dynamic_import_pattern, content))

        return {
            "exports": list(set(exports)),
            "dependencies": list(set(dependencies))
        }