import unittest
import os
import sys

# Add the root directory to sys.path so tests can see the parsers module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from parsers.ts_js_parser import TsJsParser

class TestTsJsParser(unittest.TestCase):
    def setUp(self):
        self.test_file = 'dummy_test.ts'
        self.parser = TsJsParser()

        # Create a complex dummy TS file mimicking Angular/React structures
        with open(self.test_file, 'w', encoding='utf-8') as f:
            f.write("""
import { Injectable } from '@angular/core';
import { MyModel } from './models/my.model';
const dynamicModule = require('moment');

// export class IgnoredCommentClass {}

/*
export function ignoredBlockFunction() {
    return true;
}
*/

@Injectable({
  providedIn: 'root'
})
export class UserService {
    constructor() {}
}

export const API_URL = 'https://api.example.com';

export default async function bootstrapApp() {
    await import('./lazy-module');
}
            """)

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_extract_ts_js(self):
        result = self.parser.extract(self.test_file)

        # Verify Exports
        self.assertIn('UserService', result['exports'])
        self.assertIn('API_URL', result['exports'])
        self.assertIn('bootstrapApp', result['exports'])
        self.assertNotIn('IgnoredCommentClass', result['exports'], "Should ignore inline comments")
        self.assertNotIn('ignoredBlockFunction', result['exports'], "Should ignore block comments")

        # Verify Dependencies
        self.assertIn('@angular/core', result['dependencies'])
        self.assertIn('./models/my.model', result['dependencies'])
        self.assertIn('moment', result['dependencies'])
        self.assertIn('./lazy-module', result['dependencies'])

if __name__ == '__main__':
    unittest.main()