import re
from typing import Dict, List
from parsers.base_parser import BaseParser

class PythonParser(BaseParser):
    extensions = ['.py'] # <-- Добавили список расширений

    def extract(self, file_path: str) -> Dict[str, List[str]]:
        content = self._read_file(file_path)
        return {
            "exports": re.findall(r'^(?:async\s+)?(?:def|class)\s+([a-zA-Z0-9_]+)', content, re.M),
            "dependencies": re.findall(r'^(?:import|from)\s+([a-zA-Z0-9_\.]+)', content, re.M)
        }