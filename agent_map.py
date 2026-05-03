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
            'dist', 'build', '.venv', 'venv'
        }
        # The core dynamically queries parsers for supported formats
        self.allowed_exts = get_supported_extensions()
        self.allowed_filenames = get_supported_filenames()

        self.map_filename = '_module_map.json'
        self.root_map_filename = 'agent_root.json'
        self.readme_filename = 'AGENT_README.md'
        self.processed_modules: List[str] = []

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
        module_data = {"directory": dir_path, "files": {}}

        for f in files:
            file_path = os.path.join(dir_path, f)
            # Pass the full filename; the registry decides whether to match by name or extension
            parser = get_parser(f)
            if parser:
                module_data["files"][f] = parser.extract(file_path)
            else:
                module_data["files"][f] = {"exports": [], "dependencies": []}

        try:
            with open(map_file_path, 'w', encoding='utf-8') as f:
                json.dump(module_data, f, indent=2)
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

        # We only generate it if it doesn't exist to allow manual extensions later
        if os.path.exists(readme_path):
            return

        content = f"""# 🤖 AI Agent Standard Operating Procedure (SOP)

## System Architecture
This repository utilizes `vc-context-builder` for hierarchical Retrieval-Augmented Generation (RAG).

## Your Workflow:
1. **DISCOVER:** Read `{self.root_map_filename}` to understand the high-level module structure.
2. **LOCATE:** Read `{self.map_filename}` inside specific directories to find required classes, functions, and dependencies.
3. **MODIFY:** Edit the actual source code files (`.py`, `.php`, `.js`, etc.).
4. **REGENERATE (CRITICAL):** After modifying ANY code, you MUST run `python agent_map.py` to update the context graphs. Do NOT edit JSON maps manually.
"""
        try:
            with open(readme_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logging.info(f"Generated Agent SOP: {self.readme_filename}")
        except IOError as e:
            logging.error(f"Failed to write {self.readme_filename}: {e}")

if __name__ == "__main__":
    builder = ContextBuilder()
    builder.run()