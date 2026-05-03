import os
import json
import logging
from typing import List

# Import our custom heuristic parser
from parsers import get_parser, get_supported_extensions, get_supported_filenames

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

class ContextBuilder:
    """
    Builds a hierarchical RAG context map for AI agents.
    Generates module-level and root-level maps to optimize token usage.
    """

    def __init__(self, root_dir: str = '.'):
        self.root_dir = root_dir
        self.ignore_dirs = {
            '.git', 'node_modules', 'vendor', '__pycache__',
            'dist', 'build', '.venv', 'venv', '.idea', '.vscode'
        }
        # The core dynamically queries parsers for supported formats
        self.allowed_exts = get_supported_extensions()
        self.allowed_filenames = get_supported_filenames()

        self.map_filename = '_module_map.json'
        self.root_map_filename = 'agent_root.json'
        self.readme_filename = 'AGENT_README.md'
        self.processed_modules: List[str] = []

        # Top-level project dirs that look like packages — used to prune
        # stdlib + third-party noise from dependency lists.
        self.own_packages = self._discover_own_packages()

    def _discover_own_packages(self) -> set:
        """Top-level dir names (e.g. 'bot', 'services') treated as the
        project's own. A dir qualifies if it has an __init__.py or any
        *.py file at its root. Stdlib + external libs are absent here, so
        the dependency filter drops them automatically."""
        own = set()
        try:
            for entry in os.listdir(self.root_dir):
                if entry in self.ignore_dirs or entry.startswith('.'):
                    continue
                path = os.path.join(self.root_dir, entry)
                if not os.path.isdir(path):
                    continue
                try:
                    contents = os.listdir(path)
                except OSError:
                    continue
                if '__init__.py' in contents or any(c.endswith('.py') for c in contents):
                    own.add(entry)
        except OSError:
            pass
        return own

    def _filter_deps(self, deps):
        """Keep only deps that look like own-project packages."""
        if not isinstance(deps, list):
            return deps
        if not self.own_packages:
            return sorted(deps)
        return sorted({d for d in deps if d in self.own_packages})

    def _scan_directories(self, current_dir: str) -> None:
        for root, dirs, files in os.walk(current_dir):
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs]

            valid_files = []
            for f in files:
                if f in self.allowed_filenames or os.path.splitext(f)[1] in self.allowed_exts:
                    valid_files.append(f)

            if not valid_files:
                continue

            self.processed_modules.append(root)

            if self._needs_update(root, valid_files):
                logging.info(f"Updating context map for: {root}")
                self._build_module_map(root, valid_files)

    def _build_module_map(self, dir_path: str, files: List[str]) -> None:
        map_file_path = os.path.join(dir_path, self.map_filename)
        rendered = {}

        for f in files:
            file_path = os.path.join(dir_path, f)
            parser = get_parser(f)
            data = parser.extract(file_path) if parser else {"exports": [], "dependencies": []}

            # Filter dependency noise (stdlib + third-party).
            data["dependencies"] = self._filter_deps(data.get("dependencies", []))

            # Skip files that have no public exports AND no own-project deps.
            # That kills empty __init__.py files + glue files an agent doesn't
            # need to know about.
            if not data.get("exports") and not data.get("dependencies"):
                continue

            rendered[f] = data

        if not rendered:
            # Whole directory has nothing interesting — drop the map entirely
            # so it doesn't sit there as a 200-byte stub that just costs tokens.
            if os.path.exists(map_file_path):
                try:
                    os.remove(map_file_path)
                except OSError:
                    pass
            return

        module_data = {"directory": dir_path, "files": rendered}
        try:
            with open(map_file_path, 'w', encoding='utf-8') as f:
                json.dump(module_data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logging.error(f"Failed to write {map_file_path}: {e}")

    def run(self) -> None:
        """Main entry point to start the scanning process."""
        logging.info("Starting vc-context-builder...")
        self._scan_directories(self.root_dir)
        self._build_root_map()
        self._generate_agent_sop()
        logging.info("Context build complete. Agent SOP is ready.")

    def _needs_update(self, dir_path: str, files: List[str]) -> bool:
        map_file_path = os.path.join(dir_path, self.map_filename)

        if not os.path.exists(map_file_path):
            return True

        try:
            map_mtime = os.path.getmtime(map_file_path)

            # 1. Проверяем, редактировали ли существующие файлы
            for f in files:
                file_path = os.path.join(dir_path, f)
                if os.path.getmtime(file_path) > map_mtime:
                    return True

            # 2. ФИКС: Проверяем, не удалили ли (или добавили) файлы
            with open(map_file_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
                old_files = set(old_data.get("files", {}).keys())
                current_files = set(files)

                if old_files != current_files:
                    return True # Состав файлов изменился, нужно обновить

        except (OSError, json.JSONDecodeError) as e:
            logging.warning(f"Error reading metadata in {dir_path}: {e}")
            return True # Если что-то сломалось, надежнее просто обновить

        return False

    def _build_root_map(self) -> None:
        root_map_path = os.path.join(self.root_dir, self.root_map_filename)
        root_data = {
            "project_root": os.path.abspath(self.root_dir),
            "modules": self.processed_modules,
            "entry_instruction": f"Read {self.readme_filename} first, then navigate modules via {self.map_filename}."
        }

        try:
            with open(root_map_path, 'w', encoding='utf-8') as f:
                json.dump(root_data, f, indent=2)
        except IOError as e:
            logging.error(f"Failed to write root map: {e}")

    def _generate_agent_sop(self) -> None:
        """Generates Standard Operating Procedure for AI Agents."""
        readme_path = os.path.join(self.root_dir, self.readme_filename)
        # Always rewrite — the SOP is part of the context-graph contract,
        # not a hand-edited doc. If you want extra notes, keep them in
        # README.md (separate file).

        content = (
            "# 🤖 Agent SOP — read this first, every time\n\n"
            "This repo ships hierarchical context graphs so you can answer\n"
            "questions and edit code WITHOUT loading the full source tree.\n\n"
            "## Cardinal rule: read narrowly, write fully\n\n"
            f"1. **Start tiny.** Read `{self.root_map_filename}` (one short JSON,\n"
            "   ~few hundred tokens). It lists every module folder. **Stop here\n"
            "   if the question is structural** (\"where is X handled?\", \"what\n"
            "   modules exist?\"). Don't fetch maps preemptively.\n\n"
            f"2. **Zoom in.** When you know which folder you need, read\n"
            f"   `<that-folder>/{self.map_filename}` — and only that one. Each map\n"
            "   lists every file's public exports (name + kind + signature +\n"
            "   docstring summary) and its own-package dependencies. That is\n"
            "   usually enough to answer \"is there already a function for X?\"\n"
            "   or \"what does file Y expose?\".\n\n"
            "3. **Open source only when editing or when summary is insufficient.**\n"
            "   The map shows shapes; the file holds the body. Don't open a\n"
            "   `.py` file just to check what it imports — the map already says.\n\n"
            "4. **Never read more than 1–3 maps in a row** before making a\n"
            "   reading-vs-acting decision. Loading every `_module_map.json`\n"
            "   defeats the point: tens of thousands of tokens of dependency\n"
            "   data when you needed two functions.\n\n"
            "## What you will NOT find in maps\n"
            "- Stdlib / third-party imports (filtered out as noise).\n"
            "- Private (`_prefixed`) helpers or nested defs.\n"
            "- Empty `__init__.py` files or pure-glue modules.\n"
            "If you need those — read the source.\n\n"
            "## Regenerating the maps\n"
            "Maps refresh automatically on every `git commit` (pre-commit\n"
            "hook). If you bypassed the hook, run:\n"
            f"```\npython3 .ai-context/agent_map.py\n```\n\n"
            "**Never hand-edit `_module_map.json` or `agent_root.json`** —\n"
            "the next commit will overwrite your changes.\n"
        )
        try:
            with open(readme_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logging.info(f"Generated Agent SOP: {self.readme_filename}")
        except IOError as e:
            logging.error(f"Failed to write {self.readme_filename}: {e}")

if __name__ == "__main__":
    builder = ContextBuilder()
    builder.run()