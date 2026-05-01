import os
import json
import logging
from typing import List

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

class ContextBuilder:
    """
    Builds a hierarchical RAG context map for AI agents.
    Generates _module_map.json in each directory containing valid code files.
    """

    def __init__(self, root_dir: str = '.'):
        self.root_dir = root_dir
        # Directories to exclude from scanning (heavy or system folders)
        self.ignore_dirs = {
            '.git', 'node_modules', 'vendor', '__pycache__',
            'dist', 'build', '.venv', 'venv'
        }
        # File extensions to analyze
        self.allowed_exts = {'.py', '.php', '.js', '.ts', '.html'}
        self.map_filename = '_module_map.json'

    def run(self) -> None:
        """Main entry point to start the scanning process."""
        logging.info("Starting vc-context-builder...")
        self._scan_directories(self.root_dir)
        logging.info("Context build complete.")

    def _scan_directories(self, current_dir: str) -> None:
        """Recursively scans directories and triggers updates if necessary."""
        for root, dirs, files in os.walk(current_dir):
            # Filter directories in-place to prevent traversing ignored paths
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs]

            # Filter valid files based on allowed extensions
            valid_files = [f for f in files if os.path.splitext(f)[1] in self.allowed_exts]

            if not valid_files:
                continue

            if self._needs_update(root, valid_files):
                logging.info(f"Updating context for: {root}")
                self._build_module_map(root, valid_files)

    def _needs_update(self, dir_path: str, files: List[str]) -> bool:
        """
        Checks if the module map needs an update by comparing modified times (mtime).

        Args:
            dir_path: The directory path being checked.
            files: List of valid filenames in the directory.

        Returns:
            bool: True if any file is newer than the existing _module_map.json.
        """
        map_file_path = os.path.join(dir_path, self.map_filename)

        if not os.path.exists(map_file_path):
            return True

        try:
            map_mtime = os.path.getmtime(map_file_path)

            for f in files:
                file_path = os.path.join(dir_path, f)
                if os.path.getmtime(file_path) > map_mtime:
                    return True
        except OSError as e:
            logging.warning(f"Error reading file metadata in {dir_path}: {e}")
            return True # Default to update on error to be safe

        return False

    def _build_module_map(self, dir_path: str, files: List[str]) -> None:
        """
        Parses files in the directory and generates the _module_map.json.
        """
        map_file_path = os.path.join(dir_path, self.map_filename)

        # STUB: Temporary logic. In the next step, we will add the actual AST/RegEx parsers here.
        try:
            with open(map_file_path, 'w', encoding='utf-8') as f:
                json.dump({"status": "pending_parser", "files": files}, f, indent=2)
        except IOError as e:
            logging.error(f"Failed to write {map_file_path}: {e}")

if __name__ == "__main__":
    builder = ContextBuilder()
    builder.run()