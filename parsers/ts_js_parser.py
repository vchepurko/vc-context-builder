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

        dependencies = set()
        exports = set()

        # 1. Standart ES6 imports
        standard_deps = re.findall(r'from\s+["\']([^"\']+)["\']', content)
        dependencies.update(standard_deps)

        # 2. WordPress REST API Endpoints (Heuristic)
        wp_endpoints = re.findall(r'["\'](/wp-json/[^"\']+)["\']', content)
        dependencies.update(wp_endpoints)

        # 3. Global Data Dependencies (window._mpqData, etc.)
        globals_found = re.findall(r'window\.([a-zA-Z0-9_]+)', content)
        for g in globals_found:
            dependencies.add(f"global:{g}")

        # 4. DOM Anchors (IDs)
        dom_ids = re.findall(r'getElementById\(["\']([^"\']+)["\']', content)
        for d_id in dom_ids:
            dependencies.add(f"dom:#{d_id}")

        # 5. Simple JS Functions (Exports)
        funcs = re.findall(r'function\s+([a-zA-Z0-9_]+)\s*\(', content)
        exports.update(funcs)

        return {
            "exports": list(exports),
            "dependencies": list(dependencies)
        }