"""Unit tests for the convention linter (Feature A).

Synthetic project trees, no third-party deps. Each test builds a tiny
fixture under a fresh tempdir, drops a ``.vc-context/conventions.json``
into it, and checks ``conventions.lint_project()`` plus the CLI/MCP
plumbing for the same code path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

import conventions  # noqa: E402
from query_engine import QueryEngine  # noqa: E402


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class _FixtureBase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-conv-")
        self.addCleanup(shutil.rmtree, self.root, True)


class LoadRulesTests(_FixtureBase):
    def test_missing_config_returns_empty(self) -> None:
        # No `.vc-context/` at all.
        self.assertEqual(conventions.load_rules(self.root), [])

    def test_malformed_json_returns_empty(self) -> None:
        _write(os.path.join(self.root, ".vc-context", "conventions.json"),
               "{not valid json")
        self.assertEqual(conventions.load_rules(self.root), [])

    def test_missing_rules_key_returns_empty(self) -> None:
        _write_json(os.path.join(self.root, ".vc-context", "conventions.json"),
                    {"other": []})
        self.assertEqual(conventions.load_rules(self.root), [])

    def test_invalid_severity_falls_back_to_warn(self) -> None:
        _write_json(os.path.join(self.root, ".vc-context", "conventions.json"),
                    {"rules": [
                        {"id": "x", "forbid_call": "print", "severity": "BOOM"}
                    ]})
        rules = conventions.load_rules(self.root)
        self.assertEqual(rules[0]["severity"], "warn")

    def test_rule_without_supported_kind_is_dropped(self) -> None:
        _write_json(os.path.join(self.root, ".vc-context", "conventions.json"),
                    {"rules": [
                        {"id": "noop", "match_path": "**/*.py", "severity": "warn"}
                    ]})
        self.assertEqual(conventions.load_rules(self.root), [])


class LintProjectTests(_FixtureBase):
    def _config(self, payload) -> None:
        _write_json(
            os.path.join(self.root, ".vc-context", "conventions.json"),
            payload,
        )

    def test_no_config_means_no_violations(self) -> None:
        _write(os.path.join(self.root, "app.py"), "import os\nprint('x')\n")
        self.assertEqual(conventions.lint_project(self.root), [])

    def test_forbid_import_top_level(self) -> None:
        _write(os.path.join(self.root, "bot", "handlers", "admin.py"),
               "import database\nfrom database.models import Foo\n")
        self._config({
            "rules": [{
                "id": "no-database-in-handlers",
                "match_path": "bot/handlers/**/*.py",
                "forbid_import": "database",
                "severity": "error",
            }]
        })
        violations = conventions.lint_project(self.root)
        self.assertEqual(len(violations), 2)
        self.assertTrue(all(v["severity"] == "error" for v in violations))
        self.assertTrue(conventions.has_error(violations))

    def test_forbid_import_skips_non_matching_path(self) -> None:
        _write(os.path.join(self.root, "services", "x.py"), "import database\n")
        self._config({
            "rules": [{
                "id": "scoped",
                "match_path": "bot/handlers/**/*.py",
                "forbid_import": "database",
                "severity": "error",
            }]
        })
        self.assertEqual(conventions.lint_project(self.root), [])

    def test_forbid_call_print_only(self) -> None:
        src = (
            "def f():\n"
            "    print('hello')\n"
            "    logger.info('ok')\n"
            "    obj.print('not me')\n"
        )
        _write(os.path.join(self.root, "main.py"), src)
        self._config({
            "rules": [{
                "id": "no-print",
                "match_path": "**/*.py",
                "forbid_call": "print",
                "severity": "warn",
            }]
        })
        violations = conventions.lint_project(self.root)
        # Bare ``print`` matches; ``obj.print`` doesn't (we only flag
        # leaf-name calls). Exactly one violation expected.
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["rule_id"], "no-print")
        self.assertFalse(conventions.has_error(violations))

    def test_glob_with_double_star_matches_root(self) -> None:
        _write(os.path.join(self.root, "top.py"), "print('x')\n")
        self._config({
            "rules": [{
                "id": "any-print",
                "match_path": "**/*.py",
                "forbid_call": "print",
                "severity": "info",
            }]
        })
        violations = conventions.lint_project(self.root)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["file"], "top.py")

    def test_unparseable_file_does_not_crash(self) -> None:
        _write(os.path.join(self.root, "broken.py"), "def oops(:\n")
        self._config({
            "rules": [{
                "id": "no-print",
                "match_path": "**/*.py",
                "forbid_call": "print",
                "severity": "warn",
            }]
        })
        # Doesn't raise; file just gets skipped.
        self.assertEqual(conventions.lint_project(self.root), [])

    def test_violations_sorted_by_file_and_line(self) -> None:
        _write(os.path.join(self.root, "b.py"), "print('b1')\nprint('b2')\n")
        _write(os.path.join(self.root, "a.py"), "print('a')\n")
        self._config({
            "rules": [{
                "id": "no-print",
                "match_path": "**/*.py",
                "forbid_call": "print",
                "severity": "warn",
            }]
        })
        violations = conventions.lint_project(self.root)
        files_lines = [(v["file"], v["line"]) for v in violations]
        self.assertEqual(files_lines, [("a.py", 1), ("b.py", 1), ("b.py", 2)])

    def test_forbid_decorator_regex_matches(self) -> None:
        _write(os.path.join(self.root, "h.py"), (
            "from aiogram import F, Router\n"
            "router = Router()\n\n"
            "@router.message(F.text)\n"
            "async def bare_text(msg): ...\n\n"
            "@router.message(F.text == 'menu')\n"
            "async def specific_text(msg): ...\n\n"
            "@router.message(SomeState.x, F.text)\n"
            "async def state_bound(msg): ...\n"
        ))
        self._config({
            "rules": [{
                "id": "no-bare-text-filter",
                "match_path": "**/*.py",
                # Forbid the EXACT bare-F.text decorator. State-bound
                # `@router.message(SomeState.x, F.text)` and value
                # comparisons must NOT trigger.
                "forbid_decorator_regex": r"^@router\.message\(F\.text\)$",
                "severity": "error",
            }]
        })
        violations = conventions.lint_project(self.root)
        # Only `bare_text` should fire — line 4.
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["line"], 4)
        self.assertEqual(violations[0]["severity"], "error")
        self.assertIn("bare-text-filter", violations[0]["rule_id"])

    def test_forbid_decorator_regex_invalid_pattern_drops_rule(self) -> None:
        _write(os.path.join(self.root, "h.py"), "@route\ndef f(): ...\n")
        self._config({
            "rules": [{
                "id": "bad-regex",
                "match_path": "**/*.py",
                "forbid_decorator_regex": "[unclosed",  # ← invalid
                "severity": "error",
            }]
        })
        # Bad regex → rule silently dropped → no violations.
        self.assertEqual(conventions.lint_project(self.root), [])


class QueryEngineLintTests(_FixtureBase):
    def test_engine_lint_violations_smokes(self) -> None:
        _write(os.path.join(self.root, "app.py"), "print('hi')\n")
        _write_json(os.path.join(self.root, ".vc-context", "conventions.json"),
                    {"rules": [{
                        "id": "no-print",
                        "match_path": "**/*.py",
                        "forbid_call": "print",
                        "severity": "warn",
                    }]})
        engine = QueryEngine(self.root)
        violations = engine.lint_violations()
        self.assertEqual(len(violations), 1)


class CliLintTests(_FixtureBase):
    def test_cli_lint_exits_one_on_error(self) -> None:
        _write(os.path.join(self.root, "h.py"), "import database\n")
        _write_json(os.path.join(self.root, ".vc-context", "conventions.json"),
                    {"rules": [{
                        "id": "no-db-import",
                        "match_path": "**/*.py",
                        "forbid_import": "database",
                        "severity": "error",
                    }]})
        cli = os.path.join(_SUBMODULE, "cli.py")
        result = subprocess.run(
            [sys.executable, cli, "--root", self.root, "lint"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1, msg=result.stderr)
        self.assertIn("no-db-import", result.stdout)

    def test_cli_lint_exit_zero_on_warn_only(self) -> None:
        _write(os.path.join(self.root, "h.py"), "print('x')\n")
        _write_json(os.path.join(self.root, ".vc-context", "conventions.json"),
                    {"rules": [{
                        "id": "no-print",
                        "match_path": "**/*.py",
                        "forbid_call": "print",
                        "severity": "warn",
                    }]})
        cli = os.path.join(_SUBMODULE, "cli.py")
        result = subprocess.run(
            [sys.executable, cli, "--root", self.root, "lint"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_cli_lint_json_output(self) -> None:
        _write(os.path.join(self.root, "h.py"), "print('x')\n")
        _write_json(os.path.join(self.root, ".vc-context", "conventions.json"),
                    {"rules": [{
                        "id": "no-print",
                        "match_path": "**/*.py",
                        "forbid_call": "print",
                        "severity": "warn",
                    }]})
        cli = os.path.join(_SUBMODULE, "cli.py")
        result = subprocess.run(
            [sys.executable, cli, "--root", self.root, "--json", "lint"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload[0]["rule_id"], "no-print")


if __name__ == "__main__":
    unittest.main()
