import os
import importlib
import logging
from parsers.base_parser import BaseParser


package_dir = os.path.dirname(__file__)
for filename in os.listdir(package_dir):
    if filename.endswith('_parser.py') and filename != 'base_parser.py':
        module_name = filename[:-3] # remove .py
        try:
            importlib.import_module(f'.{module_name}', package=__name__)
        except Exception as e:
            logging.error(f"Failed to auto-load parser {module_name}: {e}")


def get_parser(filename: str) -> BaseParser:
    return BaseParser.get_parser(filename)


def get_supported_extensions() -> set:
    return BaseParser.get_supported_extensions()


def get_supported_filenames() -> set:
    return BaseParser.get_supported_filenames()
