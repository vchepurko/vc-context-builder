"""Unit tests for ``find_locale_drift`` — parity audit over the
locale index."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

import indexers.locale_index as locale_index


class FindDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-drift-")
        # uk/common.json has both keys; en/common.json drops `hello`.
        # Both languages own admin.json; en drops `dashboard`.
        for lang, ns, kv in [
            ("uk", "common", {"hello": "Привіт", "bye": "Бувай"}),
            ("en", "common", {"bye": "Bye"}),
            ("uk", "admin", {"dashboard": "Панель", "users": "Користувачі"}),
            ("en", "admin", {"users": "Users"}),
        ]:
            d = os.path.join(self.root, "locales", lang)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{ns}.json"), "w", encoding="utf-8") as fh:
                import json

                json.dump(kv, fh)
        self.index = locale_index.build_locale_index(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_drift_lists_missing_keys(self) -> None:
        drift = locale_index.find_drift(self.index)
        keys = {r["key"] for r in drift}
        self.assertIn("hello", keys)
        self.assertIn("dashboard", keys)
        # Fully-translated keys are absent.
        self.assertNotIn("bye", keys)
        self.assertNotIn("users", keys)

    def test_record_shape(self) -> None:
        drift = locale_index.find_drift(self.index)
        hello = next(r for r in drift if r["key"] == "hello")
        self.assertEqual(hello["namespace"], "common")
        self.assertEqual(hello["present"], ["uk"])
        self.assertEqual(hello["missing"], ["en"])

    def test_namespace_scope(self) -> None:
        """``namespace="admin"`` filters out 'common' drift."""
        only_admin = locale_index.find_drift(self.index, namespace="admin")
        keys = {r["key"] for r in only_admin}
        self.assertEqual(keys, {"dashboard"})

    def test_parity_ok_returns_empty(self) -> None:
        """When everything is translated, list is empty."""
        # Drop the half-translated keys.
        for r in [self.root + "/locales/en/common.json", self.root + "/locales/en/admin.json"]:
            with open(r, "w", encoding="utf-8") as fh:
                fh.write(
                    '{"bye": "Bye", "users": "Users"}'
                    if "common" in r
                    else '{"users": "Users", "dashboard": "Dashboard"}'
                )
        # Rebuild with fully-translated en files.
        import json

        with open(self.root + "/locales/en/common.json", "w", encoding="utf-8") as fh:
            json.dump({"hello": "Hello", "bye": "Bye"}, fh)
        with open(self.root + "/locales/en/admin.json", "w", encoding="utf-8") as fh:
            json.dump({"users": "Users", "dashboard": "Dashboard"}, fh)
        idx = locale_index.build_locale_index(self.root)
        self.assertEqual(locale_index.find_drift(idx), [])


if __name__ == "__main__":
    unittest.main()
