import re
from parsers.base_parser import BaseParser

class PhpParser(BaseParser):
    """
    Advanced heuristic parser for PHP files.
    Strips comments before parsing to mimic AST-level precision.
    Specially tuned for WordPress and WooCommerce hooks.
    """

    extensions = ['.php', '.inc']

    def extract(self, file_path: str) -> dict[str, list[str]]:
        content = self._read_file(file_path)

        if not content:
            return {"exports": [], "dependencies": []}

        # 1. Pre-processing: Remove all types of PHP comments
        content = re.sub(r'/\*[\s\S]*?\*/', '', content) # Multi-line /* ... */
        content = re.sub(r'//.*', '', content)           # Single-line //
        content = re.sub(r'#.*', '', content)            # Single-line #

        exports = []
        dependencies = []

        # 2. Extract Exports (Classes, Interfaces, Traits, Functions)
        export_pattern = r'(?:class|interface|trait|function)\s+([a-zA-Z0-9_]+)'
        exports.extend(re.findall(export_pattern, content))

        # 3. Extract Namespace Dependencies (use App\Model)
        use_pattern = r'use\s+([a-zA-Z0-9_\\]+)'
        dependencies.extend(re.findall(use_pattern, content))

        # 4. Extract File Dependencies (require, include)
        file_dep_pattern = r'(?:require|include)(?:_once)?\s*[\'"]([a-zA-Z0-9_/\.\-]+)[\'"]'
        dependencies.extend(re.findall(file_dep_pattern, content))

        # 5. Extract WordPress / WooCommerce Hooks
        # Captures action/filter names from add_action, do_action, apply_filters
        wp_hook_pattern = r'(?:add_action|apply_filters|do_action)\s*\(\s*[\'"]([a-zA-Z0-9_-]+)[\'"]'
        dependencies.extend(re.findall(wp_hook_pattern, content))

        return {
            "exports": list(set(exports)),
            "dependencies": list(set(dependencies))
        }
