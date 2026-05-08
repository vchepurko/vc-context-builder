"""Tests for the optional TypeScript AST extractor (Feature Q).

Two layers:

1. **Wrapper unit tests** — exercise `_ts_ast.is_enabled` /
   `is_available` / `parse` with mocked subprocess / filesystem.
   No Node required.

2. **Integration test** — end-to-end run against the real Node
   extractor, **skipped when Node + typescript aren't on PATH**.
   This is the test that actually proves the JSON wire format.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
sys.path.insert(0, _SUBMODULE)

from parsers import _ts_ast


def _has_node_with_typescript() -> bool:
    """Probe: do we have node + a typescript install reachable?"""
    if shutil.which("node") is None:
        return False
    try:
        proc = subprocess.run(
            ["node", "-e", "require('typescript')"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


class IsEnabledTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-ts-ast-")
        self.addCleanup(shutil.rmtree, self.root, True)
        # Wipe the cache between tests so a previous run's "available"
        # flag doesn't leak.
        _ts_ast._AVAIL_CACHE.clear()

    def test_disabled_by_default(self) -> None:
        self.assertFalse(_ts_ast.is_enabled(self.root))

    def test_enabled_when_conventions_say_so(self) -> None:
        os.makedirs(os.path.join(self.root, ".vc-context"))
        with open(os.path.join(self.root, ".vc-context", "conventions.json"), "w") as fh:
            json.dump({"typescript_ast": {"enabled": True}}, fh)
        self.assertTrue(_ts_ast.is_enabled(self.root))

    def test_explicit_false_stays_disabled(self) -> None:
        os.makedirs(os.path.join(self.root, ".vc-context"))
        with open(os.path.join(self.root, ".vc-context", "conventions.json"), "w") as fh:
            json.dump({"typescript_ast": {"enabled": False}}, fh)
        self.assertFalse(_ts_ast.is_enabled(self.root))

    def test_malformed_conventions_treats_as_disabled(self) -> None:
        os.makedirs(os.path.join(self.root, ".vc-context"))
        with open(os.path.join(self.root, ".vc-context", "conventions.json"), "w") as fh:
            fh.write("{not json")
        self.assertFalse(_ts_ast.is_enabled(self.root))


class IsAvailableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-ts-ast-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _ts_ast._AVAIL_CACHE.clear()

    def test_no_node_returns_false(self) -> None:
        with mock.patch("parsers._ts_ast.shutil.which", return_value=None):
            self.assertFalse(_ts_ast.is_available(self.root))

    def test_local_typescript_short_circuits(self) -> None:
        # Drop a stub package.json so the local-install branch trips.
        ts_dir = os.path.join(self.root, "node_modules", "typescript")
        os.makedirs(ts_dir)
        with open(os.path.join(ts_dir, "package.json"), "w") as fh:
            fh.write("{}")
        with mock.patch("parsers._ts_ast.shutil.which", return_value="/usr/bin/node"):
            self.assertTrue(_ts_ast.is_available(self.root))

    def test_result_is_cached(self) -> None:
        # Prime the cache to True via the global-probe path; subsequent
        # calls must NOT re-invoke subprocess.
        with mock.patch("parsers._ts_ast.shutil.which", return_value="/usr/bin/node"):
            with mock.patch("parsers._ts_ast.subprocess.run") as run:
                run.return_value = mock.MagicMock(returncode=0)
                _ts_ast.is_available(self.root)
            with mock.patch("parsers._ts_ast.subprocess.run") as run:
                _ts_ast.is_available(self.root)
                run.assert_not_called()


class ParseFallbackTests(unittest.TestCase):
    """`parse` must degrade silently to ``None`` on any failure mode —
    the regex parser is the safety net, never crash agent_map."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-ts-ast-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _ts_ast._AVAIL_CACHE.clear()

    def test_returns_none_when_unavailable(self) -> None:
        with mock.patch("parsers._ts_ast.is_available", return_value=False):
            self.assertIsNone(_ts_ast.parse("any.ts", self.root))

    def test_returns_none_on_node_failure(self) -> None:
        proc = mock.MagicMock(returncode=2, stdout="", stderr="boom")
        with (
            mock.patch("parsers._ts_ast.is_available", return_value=True),
            mock.patch("parsers._ts_ast.subprocess.run", return_value=proc),
        ):
            self.assertIsNone(_ts_ast.parse("any.ts", self.root))

    def test_returns_none_on_garbled_stdout(self) -> None:
        proc = mock.MagicMock(returncode=0, stdout="not json", stderr="")
        with (
            mock.patch("parsers._ts_ast.is_available", return_value=True),
            mock.patch("parsers._ts_ast.subprocess.run", return_value=proc),
        ):
            self.assertIsNone(_ts_ast.parse("any.ts", self.root))

    def test_returns_records_on_valid_payload(self) -> None:
        records = [{"name": "C", "role": "ng-component", "selector": "app-c"}]
        proc = mock.MagicMock(returncode=0, stdout=json.dumps(records), stderr="")
        with (
            mock.patch("parsers._ts_ast.is_available", return_value=True),
            mock.patch("parsers._ts_ast.subprocess.run", return_value=proc),
        ):
            self.assertEqual(_ts_ast.parse("any.ts", self.root), records)


@unittest.skipUnless(
    _has_node_with_typescript(),
    "Node + typescript not installed — skipping integration test",
)
class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-ts-ast-int-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _ts_ast._AVAIL_CACHE.clear()

    def test_extractor_returns_decorator_metadata(self) -> None:
        ts_path = os.path.join(self.root, "cart.service.ts")
        with open(ts_path, "w") as fh:
            fh.write(
                "import {Injectable} from '@angular/core';\n"
                "@Injectable({providedIn: 'root'})\n"
                "export class CartService {}\n"
            )
        records = _ts_ast.parse(ts_path, self.root)
        self.assertIsNotNone(records)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "CartService")
        self.assertEqual(records[0]["role"], "ng-service")
        self.assertEqual(records[0]["providedIn"], "root")


if __name__ == "__main__":
    unittest.main()
