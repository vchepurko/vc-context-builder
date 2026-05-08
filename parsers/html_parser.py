import re
from typing import Dict, List

from parsers.base_parser import BaseParser


class HtmlParser(BaseParser):
    """Parses HTML files to extract asset links and IDs."""

    extensions = (".html", ".htm")

    def extract(self, file_path: str) -> Dict[str, List[str]]:
        content = self._read_file(file_path)
        if not content:
            return {"exports": [], "dependencies": []}

        # Strip comments content = re.sub(r'', '', content)

        dependencies = set()
        exports = set()

        # Dependencies: script src and link href
        scripts = re.findall(r'<script\s+[^>]*src=["\']([^"\']+)["\']', content)
        links = re.findall(r'<link\s+[^>]*href=["\']([^"\']+)["\']', content)
        dependencies.update(scripts)
        dependencies.update(links)

        # Exports: IDs only (unique points of interest)
        ids = re.findall(r'id=["\']([^"\']+)["\']', content)
        exports.update(ids)

        return {"exports": list(exports), "dependencies": list(dependencies)}
