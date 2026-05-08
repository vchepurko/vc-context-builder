"""Unit tests for ``QueryEngine`` against synthetic fixtures."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

# The submodule keeps `query_engine.py` at its root, sibling to this tests/ dir.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

from query_engine import QueryEngine


def _write(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class FixtureMixin:
    """Builds a tiny but realistic artifact tree under ``self.root``."""

    def _make_fixture(self) -> str:
        tmp = tempfile.mkdtemp(prefix="vc-context-")
        self.addCleanup(self._cleanup, tmp)

        agent_root = {
            "project_root": tmp,
            "modules": [".", "./pkg_a", "./pkg_b"],
            "entry_instruction": "...",
            "roles": {
                "webhook": ["liqpay_callback", "monobank_callback"],
                "route": ["index_route"],
            },
        }
        _write(os.path.join(tmp, "agent_root.json"), agent_root)

        agent_symbols = {
            "liqpay_callback": {
                "file": "pkg_a/webhooks.py",
                "kind": "async-func",
                "params": "(request)",
                "doc": "Handle LiqPay webhook.\nMore detail.",
                "role": "webhook",
            },
            "monobank_callback": {
                "file": "pkg_a/webhooks.py",
                "kind": "async-func",
                "role": "webhook",
            },
            "index_route": {
                "file": "pkg_b/routes.py",
                "kind": "async-func",
                "role": "route",
            },
            "helper_lib": {
                "file": "pkg_b/utils.py",
                "kind": "func",
            },
        }
        _write(os.path.join(tmp, "agent_symbols.json"), agent_symbols)

        # Module map for pkg_a
        _write(
            os.path.join(tmp, "pkg_a", "_module_map.json"),
            {
                "directory": "./pkg_a",
                "files": {
                    "webhooks.py": {
                        "exports": [
                            {
                                "name": "liqpay_callback",
                                "kind": "async-func",
                                "params": "(request)",
                                "doc": "Handle LiqPay webhook.\nMore detail.",
                                "role": "webhook",
                            },
                            {
                                "name": "monobank_callback",
                                "kind": "async-func",
                                "role": "webhook",
                            },
                        ],
                        "dependencies": ["pkg_b"],
                    },
                },
            },
        )

        # Module map for pkg_b — pkg_b/routes.py imports pkg_a.
        _write(
            os.path.join(tmp, "pkg_b", "_module_map.json"),
            {
                "directory": "./pkg_b",
                "files": {
                    "routes.py": {
                        "exports": [{"name": "index_route", "kind": "async-func", "role": "route"}],
                        "dependencies": ["pkg_a", "liqpay_callback"],
                    },
                    "utils.py": {
                        "exports": [{"name": "helper_lib", "kind": "func"}],
                        "dependencies": [],
                    },
                },
            },
        )

        return tmp

    def _cleanup(self, tmp: str) -> None:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


class QueryEngineTests(FixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_fixture()
        self.engine = QueryEngine(self.root)

    # ------------------------------------------------------------------
    # find_symbol
    # ------------------------------------------------------------------

    def test_find_symbol_hit_returns_full_record(self) -> None:
        entry = self.engine.find_symbol("liqpay_callback")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["file"], "pkg_a/webhooks.py")
        self.assertEqual(entry["kind"], "async-func")
        self.assertEqual(entry["role"], "webhook")
        self.assertIn("doc", entry)

    def test_find_symbol_miss_returns_none(self) -> None:
        self.assertIsNone(self.engine.find_symbol("does_not_exist"))

    def test_find_symbol_returns_copy(self) -> None:
        entry = self.engine.find_symbol("liqpay_callback")
        entry["file"] = "mutated"
        again = self.engine.find_symbol("liqpay_callback")
        self.assertEqual(again["file"], "pkg_a/webhooks.py")

    # ------------------------------------------------------------------
    # find_by_role
    # ------------------------------------------------------------------

    def test_find_by_role_hit(self) -> None:
        names = self.engine.find_by_role("webhook")
        self.assertEqual(names, ["liqpay_callback", "monobank_callback"])

    def test_find_by_role_miss_returns_empty_list(self) -> None:
        self.assertEqual(self.engine.find_by_role("nonexistent"), [])

    # ------------------------------------------------------------------
    # who_calls
    # ------------------------------------------------------------------

    def test_who_calls_finds_importers(self) -> None:
        callers = self.engine.who_calls("liqpay_callback")
        files = [c["file"] for c in callers]
        # pkg_b/routes.py imports both the symbol name and pkg_a — it
        # has to appear; pkg_a/webhooks.py (defining file) must NOT.
        self.assertIn("pkg_b/routes.py", files)
        self.assertNotIn("pkg_a/webhooks.py", files)

    def test_who_calls_empty_for_unknown_symbol(self) -> None:
        self.assertEqual(self.engine.who_calls("never_imported"), [])

    # ------------------------------------------------------------------
    # summarise_module
    # ------------------------------------------------------------------

    def test_summarise_module_strips_params_and_keeps_first_doc_line(self) -> None:
        summary = self.engine.summarise_module("pkg_a")
        self.assertIsNotNone(summary)
        web = summary["files"]["webhooks.py"]
        liqpay = next(e for e in web["exports"] if e["name"] == "liqpay_callback")
        self.assertNotIn("params", liqpay)
        self.assertEqual(liqpay["doc"], "Handle LiqPay webhook.")
        self.assertEqual(liqpay["role"], "webhook")

    def test_summarise_module_unknown_folder_returns_none(self) -> None:
        self.assertIsNone(self.engine.summarise_module("nope"))

    def test_summarise_module_accepts_leading_dot_slash(self) -> None:
        self.assertIsNotNone(self.engine.summarise_module("./pkg_b"))

    # ------------------------------------------------------------------
    # list_*
    # ------------------------------------------------------------------

    def test_list_roles_counts_by_bucket(self) -> None:
        counts = self.engine.list_roles()
        self.assertEqual(counts["webhook"], 2)
        self.assertEqual(counts["route"], 1)

    def test_list_modules_returns_recorded_order(self) -> None:
        modules = self.engine.list_modules()
        self.assertEqual(modules, [".", "./pkg_a", "./pkg_b"])


if __name__ == "__main__":
    unittest.main()
