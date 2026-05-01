import unittest
import os
from file_parser import FileParser

class TestFileParser(unittest.TestCase):

    def setUp(self):
        # Create temporary dummy files for testing
        self.php_file = 'dummy_test.php'
        with open(self.php_file, 'w', encoding='utf-8') as f:
            f.write("""
            <?php
            use App\\Controllers\\QuizController;
            require_once 'helpers.php';
            
            class MyQuizEngine {
                public function calculate_score() {}
            }
            
            add_action('woocommerce_init', 'my_custom_init');
            """)

    def tearDown(self):
        # Cleanup after tests
        if os.path.exists(self.php_file):
            os.remove(self.php_file)

    def test_php_parsing(self):
        result = FileParser.parse(self.php_file, '.php')

        # Check exports
        self.assertIn('MyQuizEngine', result['exports'])

        # Check dependencies and hooks
        self.assertIn('App\\Controllers\\QuizController', result['dependencies'])
        self.assertIn('helpers.php', result['dependencies'])
        self.assertIn('woocommerce_init', result['dependencies'])

if __name__ == '__main__':
    unittest.main()