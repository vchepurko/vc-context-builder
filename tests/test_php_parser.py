import unittest
import os
import sys

# Add the root directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from parsers.php_parser import PhpParser

class TestPhpParser(unittest.TestCase):
    def setUp(self):
        self.test_file = 'dummy_test.php'
        self.parser = PhpParser()

        # Create a complex dummy PHP file mimicking a WordPress/WooCommerce plugin
        with open(self.test_file, 'w', encoding='utf-8') as f:
            f.write("""<?php
namespace App\\Controllers;

use App\\Models\\QuizModel;
use App\\Helpers\\DataSanitizer;

require_once 'vendor/autoload.php';

// class IgnoredCommentClass {}
# function ignored_hash_function() {}

/*
add_action('init', 'ignored_block_action');
*/

interface QuizInterface {
    public function render();
}

trait Loggable {
    public function log() {}
}

class QuizController implements QuizInterface {
    use Loggable;

    public function __construct() {
        add_action('woocommerce_before_cart', [$this, 'display_quiz_banner']);
        $value = apply_filters('vc_quiz_custom_filter', $value);
    }

    public function render() {}
}

function vc_quiz_global_helper() {}
""")

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_extract_php(self):
        result = self.parser.extract(self.test_file)

        # Verify Exports
        self.assertIn('QuizInterface', result['exports'])
        self.assertIn('Loggable', result['exports'])
        self.assertIn('QuizController', result['exports'])
        self.assertIn('vc_quiz_global_helper', result['exports'])

        # Verify Comments are Ignored
        self.assertNotIn('IgnoredCommentClass', result['exports'])
        self.assertNotIn('ignored_hash_function', result['exports'])
        self.assertNotIn('ignored_block_action', result['dependencies'])

        # Verify Dependencies (Uses, Requires, and WordPress Hooks)
        self.assertIn('App\\Models\\QuizModel', result['dependencies'])
        self.assertIn('vendor/autoload.php', result['dependencies'])
        self.assertIn('woocommerce_before_cart', result['dependencies'])
        self.assertIn('vc_quiz_custom_filter', result['dependencies'])

if __name__ == '__main__':
    unittest.main()