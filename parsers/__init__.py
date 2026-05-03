import os
import importlib
import logging
from parsers.base_parser import BaseParser

# Автоматически импортируем все файлы *_parser.py в текущей директории
package_dir = os.path.dirname(__file__)
for filename in os.listdir(package_dir):
    if filename.endswith('_parser.py') and filename != 'base_parser.py':
        module_name = filename[:-3] # убираем .py
        try:
            importlib.import_module(f'.{module_name}', package=__name__)
        except Exception as e:
            logging.error(f"Failed to auto-load parser {module_name}: {e}")

# Экспортируем удобный метод для agent_map.py
def get_parser(ext: str) -> BaseParser:
    return BaseParser.get_parser(ext)