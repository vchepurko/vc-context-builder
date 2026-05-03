import re
from typing import Dict, List
from parsers.base_parser import BaseParser

class DevOpsParser(BaseParser):
    """
    Parses infrastructure and CI/CD files to provide environment context to the AI.
    """
    extensions = ['.yml', '.yaml']
    filenames = ['Dockerfile', 'Makefile', 'docker-compose.yml']

    def extract(self, file_path: str) -> Dict[str, List[str]]:
        content = self._read_file(file_path)
        result = {"exports": [], "dependencies": []}

        # Handle Dockerfile
        if 'Dockerfile' in file_path:
            result["dependencies"] = re.findall(r'^FROM\s+([^\s]+)', content, re.M)
            result["exports"] = re.findall(r'^EXPOSE\s+(\d+)', content, re.M)

        # Handle Docker Compose & GitHub Actions (YAML)
        elif file_path.endswith(('.yml', '.yaml')):
            # Find Docker images being used
            result["dependencies"] = re.findall(r'image:\s*([^\s]+)', content)
            # Find defined services or jobs
            services = re.findall(r'^  ([a-zA-Z0-9_-]+):$', content, re.M)
            jobs = re.findall(r'jobs:\s*\n\s+([a-zA-Z0-9_-]+):', content)
            result["exports"] = list(set(services + jobs))

        # Handle Makefile
        elif 'Makefile' in file_path:
            # Find make targets
            result["exports"] = re.findall(r'^([a-zA-Z0-9_-]+):', content, re.M)

        return result