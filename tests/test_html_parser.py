import unittest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from parsers.html_parser import HtmlParser

class TestHtmlParser(unittest.TestCase):
    def setUp(self):
        self.parser = HtmlParser()
        self.test_file = 'temp_index.html'

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_extract_assets(self):
        with open(self.test_file, 'w') as f:
            f.write('<link rel="stylesheet" href="style.css"><script src="app.js"></script>')

        result = self.parser.extract(self.test_file)
        self.assertIn('style.css', result['dependencies'])
        self.assertIn('app.js', result['dependencies'])

    def test_extract_ids(self):
        with open(self.test_file, 'w') as f:
            f.write('<div id="quiz-app"></div><footer id="main-footer"></footer>')

        result = self.parser.extract(self.test_file)
        self.assertIn('quiz-app', result['exports'])
        self.assertIn('main-footer', result['exports'])