"""Tests for ContextBuilder.ignore_dirs resolution.

Three behaviours we verify:

1. Default set includes ``.ai-context`` / ``.vc-context`` so a parent
   project doesn't re-index its own vc-context-builder submodule.
2. ``conventions.json["ignore_dirs"]`` REPLACES the defaults when no
   prefix is used (full opt-out).
3. ``+entry`` ADDS to the defaults; ``-entry`` REMOVES.
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

from agent_map import ContextBuilder


def _write_conventions(root: str, payload: dict) -> None:
    conv_dir = os.path.join(root, ".vc-context")
    os.makedirs(conv_dir, exist_ok=True)
    with open(os.path.join(conv_dir, "conventions.json"), "w") as fh:
        json.dump(payload, fh)


class IgnoreDirsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-ignore-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_defaults_include_submodule_self_skip(self) -> None:
        result = ContextBuilder._resolve_ignore_dirs(self.root)
        self.assertIn(".ai-context", result)
        self.assertIn(".vc-context", result)
        self.assertIn("node_modules", result)
        self.assertIn(".git", result)

    def test_no_conventions_returns_full_defaults(self) -> None:
        result = ContextBuilder._resolve_ignore_dirs(self.root)
        self.assertEqual(result, set(ContextBuilder.DEFAULT_IGNORE_DIRS))

    def test_pure_list_replaces_defaults(self) -> None:
        _write_conventions(self.root, {"ignore_dirs": ["my-build", "logs"]})
        result = ContextBuilder._resolve_ignore_dirs(self.root)
        self.assertEqual(result, {"my-build", "logs"})
        # Defaults are NOT silently retained.
        self.assertNotIn("node_modules", result)

    def test_additive_prefix_extends_defaults(self) -> None:
        _write_conventions(self.root, {"ignore_dirs": ["+coverage", "+e2e"]})
        result = ContextBuilder._resolve_ignore_dirs(self.root)
        self.assertIn("coverage", result)
        self.assertIn("e2e", result)
        # Defaults still present.
        self.assertIn("node_modules", result)
        self.assertIn(".ai-context", result)

    def test_subtractive_prefix_removes_defaults(self) -> None:
        # Some projects vendor things under 'vendor/' and want to KEEP
        # indexing it (e.g. a monorepo with hand-managed shared code).
        _write_conventions(self.root, {"ignore_dirs": ["-vendor"]})
        result = ContextBuilder._resolve_ignore_dirs(self.root)
        self.assertNotIn("vendor", result)
        self.assertIn("node_modules", result)

    def test_mixed_replace_with_add(self) -> None:
        _write_conventions(
            self.root,
            {
                "ignore_dirs": ["custom", "+extra"],
            },
        )
        result = ContextBuilder._resolve_ignore_dirs(self.root)
        # Replacement set, with additive entry layered on.
        self.assertEqual(result, {"custom", "extra"})

    def test_malformed_conventions_returns_defaults(self) -> None:
        conv_dir = os.path.join(self.root, ".vc-context")
        os.makedirs(conv_dir)
        with open(os.path.join(conv_dir, "conventions.json"), "w") as fh:
            fh.write("{ not json")
        result = ContextBuilder._resolve_ignore_dirs(self.root)
        self.assertEqual(result, set(ContextBuilder.DEFAULT_IGNORE_DIRS))

    def test_non_list_ignore_dirs_falls_back_to_defaults(self) -> None:
        _write_conventions(self.root, {"ignore_dirs": "node_modules"})
        result = ContextBuilder._resolve_ignore_dirs(self.root)
        self.assertEqual(result, set(ContextBuilder.DEFAULT_IGNORE_DIRS))


if __name__ == "__main__":
    unittest.main()
