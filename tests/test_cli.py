"""End-to-end smoke tests for the ``vc-context`` CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from test_query_engine import FixtureMixin  # type: ignore[import-not-found]


_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
_CLI = os.path.join(_SUBMODULE, "cli.py")


def _run(root: str, *cli_args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, _CLI, "--root", root, *cli_args]
    return subprocess.run(cmd, capture_output=True, text=True)


class CliTests(FixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_fixture()

    def test_find_hit_json_shape(self) -> None:
        result = _run(self.root, "--json", "find", "liqpay_callback")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["name"], "liqpay_callback")
        self.assertEqual(payload["file"], "pkg_a/webhooks.py")

    def test_find_miss_exits_one(self) -> None:
        result = _run(self.root, "find", "no_such_thing")
        self.assertEqual(result.returncode, 1)

    def test_role_hit_lists_names(self) -> None:
        result = _run(self.root, "--json", "role", "webhook")
        self.assertEqual(result.returncode, 0)
        names = json.loads(result.stdout)
        self.assertIn("liqpay_callback", names)

    def test_role_miss_exits_one(self) -> None:
        result = _run(self.root, "role", "ghost")
        self.assertEqual(result.returncode, 1)

    def test_module_summary_has_files_key(self) -> None:
        result = _run(self.root, "--json", "module", "pkg_a")
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertIn("files", payload)
        self.assertIn("webhooks.py", payload["files"])

    def test_module_unknown_exits_one(self) -> None:
        result = _run(self.root, "module", "nope")
        self.assertEqual(result.returncode, 1)

    def test_roles_list(self) -> None:
        result = _run(self.root, "--json", "roles")
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["webhook"], 2)

    def test_modules_list(self) -> None:
        result = _run(self.root, "--json", "modules")
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertIn("./pkg_a", payload)


if __name__ == "__main__":
    unittest.main()
