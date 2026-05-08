import re
from typing import Dict, List

from parsers.base_parser import BaseParser


class CssParser(BaseParser):
    """Parses CSS files to extract class names and imports."""

    extensions = (".css", ".scss", ".sass")

    def extract(self, file_path: str) -> Dict[str, List[str]]:
        content = self._read_file(file_path)
        if not content:
            return {"exports": [], "dependencies": []}

        # Remove comments /* ... */
        content = re.sub(r"/\*[\s\S]*?\*/", "", content)

        dependencies = set()
        exports = set()

        # Dependencies: @import "style.css";
        imports = re.findall(r'@import\s+["\']([^"\']+)["\']', content)
        dependencies.update(imports)

        # Exports: CSS Classes (.my-class) and IDs (#my-id)
        # We catch anything starting with . or # followed by name before {
        selectors = re.findall(r"[\.#]([a-zA-Z0-9_-]+)\s*(?:,|\{)", content)
        exports.update(selectors)

        return {"exports": list(exports), "dependencies": list(dependencies)}
