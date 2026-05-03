from abc import ABC, abstractmethod
from typing import Dict, List, Type
import logging

class BaseParser(ABC):
    """Abstract base class for all language-specific parsers."""

    # Здесь автоматически будут храниться все парсеры: {'.py': PythonParser(), ...}
    _registry: Dict[str, 'BaseParser'] = {}

    @classmethod
    def __init_subclass__(cls, **kwargs):
        """Magic method that auto-registers any subclass of BaseParser."""
        super().__init_subclass__(**kwargs)

        # Класс-наследник обязан иметь атрибут extensions
        if hasattr(cls, 'extensions'):
            parser_instance = cls()
            for ext in cls.extensions:
                cls._registry[ext] = parser_instance

    @classmethod
    def get_parser(cls, ext: str) -> 'BaseParser':
        """Retrieve a parser by file extension."""
        return cls._registry.get(ext)

    @abstractmethod
    def extract(self, file_path: str) -> Dict[str, List[str]]:
        pass

    def _read_file(self, file_path: str) -> str:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logging.warning(f"Failed to read {file_path}: {e}")
            return ""