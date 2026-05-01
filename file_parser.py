import re
import logging
from typing import Dict, List

class FileParser:
    """
    Heuristic regex-based parser to extract context from source files.
    Designed specifically for LLM context injection (RAG), not compiler-grade AST.
    """

    @staticmethod
    def parse(file_path: str, ext: str) -> Dict[str, List[str]]:
        result = {
            "exports": [],       # Classes, functions, interfaces
            "dependencies": []   # Imports, includes, hooks
        }

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logging.warning(f"Failed to read {file_path}: {e}")
            return result

        if ext == '.py':
            result["exports"] = re.findall(r'^(?:async\s+)?(?:def|class)\s+([a-zA-Z0-9_]+)', content, re.M)
            result["dependencies"] = re.findall(r'^(?:import|from)\s+([a-zA-Z0-9_\.]+)', content, re.M)

        elif ext == '.php':
            result["exports"] = re.findall(r'(?:class|interface|trait|function)\s+([a-zA-Z0-9_]+)', content)
            # Match use, include, require
            deps = re.findall(r'(?:use|include(?:_once)?|require(?:_once)?)\s+[\'"]?([a-zA-Z0-9_\\/\.]+)[\'"]?;', content)
            # Specific for WordPress/WooCommerce: capture hooks
            hooks = re.findall(r'(?:add_action|apply_filters|do_action)\(\s*[\'"]([a-zA-Z0-9_-]+)[\'"]', content)
            result["dependencies"] = list(set(deps + hooks)) # Unique values

        elif ext in ['.js', '.ts']:
            result["exports"] = re.findall(r'export\s+(?:default\s+)?(?:class|const|let|var|function|interface|type)\s+([a-zA-Z0-9_]+)', content)
            result["dependencies"] = re.findall(r'import\s+.*from\s+[\'"]([a-zA-Z0-9_/\.-]+)[\'"]', content)

        return result