"""Tests for the targeted JSON parser.

Each known-config-file shape gets its own test so a future contributor
can add a new one (e.g. ``Cargo.toml``-equivalent) without touching
existing logic.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from parsers.json_parser import (
    JsonParser,
    _parse_composer_json,
    _parse_package_json,
    _parse_tsconfig_json,
)


class _TmpFileMixin:
    def _write(self, name: str, content: str) -> str:
        tmp = tempfile.mkdtemp(prefix="vc-json-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path


class PackageJsonTests(unittest.TestCase):
    def test_name_and_version_combined(self) -> None:
        out = _parse_package_json({"name": "my-app", "version": "1.2.3"})
        self.assertEqual(out["exports"], ["my-app@1.2.3"])

    def test_name_only(self) -> None:
        out = _parse_package_json({"name": "my-app"})
        self.assertEqual(out["exports"], ["my-app"])

    def test_dependencies_combined_and_sorted(self) -> None:
        out = _parse_package_json(
            {
                "name": "x",
                "dependencies": {"react": "^18", "lodash": "^4"},
                "devDependencies": {"typescript": "^5"},
                "peerDependencies": {"react": "^18"},
            }
        )
        # Sorted, deduplicated across all three blocks.
        self.assertEqual(out["dependencies"], ["lodash", "react", "typescript"])

    def test_missing_blocks_empty(self) -> None:
        out = _parse_package_json({})
        self.assertEqual(out, {"exports": [], "dependencies": []})


class TsconfigJsonTests(unittest.TestCase):
    def test_paths_become_exports(self) -> None:
        out = _parse_tsconfig_json(
            {
                "compilerOptions": {
                    "paths": {"@app/*": ["src/app/*"], "@env/*": ["src/env/*"]},
                },
            }
        )
        self.assertEqual(out["exports"], ["@app/*", "@env/*"])

    def test_extends_and_baseurl_become_deps(self) -> None:
        out = _parse_tsconfig_json(
            {
                "extends": "./tsconfig.base.json",
                "compilerOptions": {"baseUrl": "./src"},
            }
        )
        self.assertEqual(
            out["dependencies"],
            ["./tsconfig.base.json", "baseUrl:./src"],
        )

    def test_empty_compiler_options(self) -> None:
        out = _parse_tsconfig_json({"compilerOptions": {}})
        self.assertEqual(out, {"exports": [], "dependencies": []})


class ComposerJsonTests(unittest.TestCase):
    def test_name_and_requires(self) -> None:
        out = _parse_composer_json(
            {
                "name": "vendor/pkg",
                "require": {"php": "^8.1", "guzzlehttp/guzzle": "^7.0"},
                "require-dev": {"phpunit/phpunit": "^10.0"},
            }
        )
        self.assertEqual(out["exports"], ["vendor/pkg"])
        self.assertEqual(
            out["dependencies"],
            ["guzzlehttp/guzzle", "php", "phpunit/phpunit"],
        )


class ParserDispatchTests(_TmpFileMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.parser = JsonParser()

    def test_unknown_filename_returns_empty(self) -> None:
        path = self._write("random.json", json.dumps({"foo": "bar"}))
        self.assertEqual(
            self.parser.extract(path),
            {"exports": [], "dependencies": []},
        )

    def test_invalid_json_returns_empty_quietly(self) -> None:
        path = self._write("package.json", "{not json")
        self.assertEqual(
            self.parser.extract(path),
            {"exports": [], "dependencies": []},
        )

    def test_non_object_top_level(self) -> None:
        path = self._write("package.json", json.dumps([1, 2, 3]))
        self.assertEqual(
            self.parser.extract(path),
            {"exports": [], "dependencies": []},
        )

    def test_package_json_end_to_end(self) -> None:
        path = self._write(
            "package.json",
            json.dumps({"name": "demo", "version": "0.1.0", "dependencies": {"lodash": "^4"}}),
        )
        out = self.parser.extract(path)
        self.assertEqual(out["exports"], ["demo@0.1.0"])
        self.assertEqual(out["dependencies"], ["lodash"])

    def test_tsconfig_json_end_to_end(self) -> None:
        path = self._write(
            "tsconfig.json",
            json.dumps(
                {
                    "extends": "./base.json",
                    "compilerOptions": {"paths": {"@core/*": ["core/*"]}},
                }
            ),
        )
        out = self.parser.extract(path)
        self.assertEqual(out["exports"], ["@core/*"])
        self.assertEqual(out["dependencies"], ["./base.json"])


class FilenameRegistrationTests(unittest.TestCase):
    def test_registered_for_each_known_filename(self) -> None:
        # Must be in the auto-registry so agent_map picks it up.
        self.assertIn("package.json", JsonParser.filenames)
        self.assertIn("tsconfig.json", JsonParser.filenames)
        self.assertIn("composer.json", JsonParser.filenames)


if __name__ == "__main__":
    unittest.main()
