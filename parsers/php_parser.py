import re
from typing import Dict, List
from parsers.base_parser import BaseParser

class PhpParser(BaseParser):
    extensions = ['.php']

    def extract(self, file_path: str) -> Dict[str, List[str]]:
        content = self._read_file(file_path)
        deps = re.findall(r'(?:use|include(?:_once)?|require(?:_once)?)\s+[\'"]?([a-zA-Z0-9_\\/\.]+)[\'"]?;', content)
        hooks = re.findall(r'(?:add_action|apply_filters|do_action)\(\s*[\'"]([a-zA-Z0-9_-]+)[\'"]', content)

        return {
            "exports": re.findall(r'(?:class|interface|trait|function)\s+([a-zA-Z0-9_]+)', content),
            "dependencies": list(set(deps + hooks))
        }