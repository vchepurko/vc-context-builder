from abc import ABC, abstractmethod
from typing import Dict, List, Type
import logging
import os

class BaseParser(ABC):
    """Abstract base class for all language-specific and infrastructure parsers."""

    _ext_registry: Dict[str, 'BaseParser'] = {}
    _file_registry: Dict[str, 'BaseParser'] = {}

    @classmethod
    def __init_subclass__(cls, **kwargs):
        """Auto-registers parsers based on their 'extensions' and 'filenames' attributes."""
        super().__init_subclass__(**kwargs)

        parser_instance = None
        if hasattr(cls, 'extensions') or hasattr(cls, 'filenames'):
            parser_instance = cls()

        if hasattr(cls, 'extensions'):
            for ext in cls.extensions:
                cls._ext_registry[ext] = parser_instance

        if hasattr(cls, 'filenames'):
            for fname in cls.filenames:
                cls._file_registry[fname] = parser_instance

    @classmethod
    def get_parser(cls, filename: str) -> 'BaseParser':
        """Retrieve a parser by exact filename first, then fallback to extension."""
        if filename in cls._file_registry:
            return cls._file_registry[filename]

        ext = os.path.splitext(filename)[1]
        return cls._ext_registry.get(ext)

    @classmethod
    def get_supported_extensions(cls) -> set:
        return set(cls._ext_registry.keys())

    @classmethod
    def get_supported_filenames(cls) -> set:
        return set(cls._file_registry.keys())

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