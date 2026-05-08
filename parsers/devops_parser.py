import re
from typing import Dict, List

from parsers.base_parser import BaseParser


class DevOpsParser(BaseParser):
    """Extracts context from infrastructure and CI/CD files."""

    extensions = (".yml", ".yaml")
    filenames = ("Dockerfile", "docker-compose.yml", "Makefile")

    def extract(self, file_path: str) -> Dict[str, List[str]]:
        content = self._read_file(file_path)

        exports = []
        dependencies = []

        # 1. Dockerfile
        if "Dockerfile" in file_path:
            # Capture EXPOSE ports, CMD or ENTRYPOINT configurations
            exports.extend(re.findall(r"^(?:EXPOSE|CMD|ENTRYPOINT)\s+(.*)", content, re.M))
            # Capture base images used
            dependencies.extend(re.findall(r"^FROM\s+([a-zA-Z0-9_/\.:-]+)", content, re.M))

        # 2. Makefile
        elif file_path.endswith("Makefile"):
            # Find make targets (e.g., "build:", "test:")
            exports.extend(re.findall(r"^([a-zA-Z0-9_-]+):", content, re.M))

        # 3. YAML (Docker Compose or GitHub Actions)
        elif file_path.endswith((".yml", ".yaml")):
            # Heuristic for Docker Compose services (keys under 'services:')
            if "services:" in content:
                # Assumes standard 2-space indentation for services
                exports.extend(re.findall(r"^  ([a-zA-Z0-9_-]+):", content, re.M))

            # Look for Docker images
            dependencies.extend(re.findall(r"image:\s*([a-zA-Z0-9_/\.:-]+)", content))

            # Look for GitHub Actions 'uses:'
            dependencies.extend(re.findall(r"uses:\s*([a-zA-Z0-9_/\.:@-]+)", content))

        return {"exports": list(set(exports)), "dependencies": list(set(dependencies))}
