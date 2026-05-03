import re
from typing import Dict, List
from parsers.base_parser import BaseParser

class TsJsParser(BaseParser):
    extensions = ['.js', '.ts']

    def extract(self, file_path: str) -> Dict[str, List[str]]:
        content = self._read_file(file_path)
        return {
            "exports": re.findall(r'export\s+(?:default\s+)?(?:class|const|let|var|function|interface|type)\s+([a-zA-Z0-9_]+)', content),
            "dependencies": re.findall(r'import\s+.*from\s+[\'"]([a-zA-Z0-9_/\.-]+)[\'"]', content)
        }