import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from parsers.css_parser import CssParser


class TestCssParser(unittest.TestCase):
    def setUp(self):
        self.parser = CssParser()
        self.test_file = "temp_style.css"

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_extract_classes_and_ids(self):
        with open(self.test_file, "w") as f:
            f.write(".quiz-card { padding: 10px; } #submit-btn, .active { color: blue; }")

        result = self.parser.extract(self.test_file)
        self.assertIn("quiz-card", result["exports"])
        self.assertIn("submit-btn", result["exports"])
        self.assertIn("active", result["exports"])

    def test_extract_imports(self):
        with open(self.test_file, "w") as f:
            f.write("@import 'variables.css'; @import \"mixins.css\";")

        result = self.parser.extract(self.test_file)
        self.assertIn("variables.css", result["dependencies"])
        self.assertIn("mixins.css", result["dependencies"])
