"""Unit tests for the locale-key index (Feature I)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

import locale_index
from query_engine import QueryEngine


def _write(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def _write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class BuildLocaleIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-locale-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_no_locales_dir_returns_empty(self) -> None:
        index = locale_index.build_locale_index(self.root)
        self.assertEqual(index, {})

    def test_single_language_single_namespace(self) -> None:
        _write(
            os.path.join(self.root, "locales", "uk", "common.json"),
            {
                "hello": "Привіт",
                "bye": "Бувай",
            },
        )
        index = locale_index.build_locale_index(self.root)
        self.assertEqual(set(index.keys()), {"hello", "bye"})
        self.assertEqual(index["hello"]["namespace"], "common")
        self.assertEqual(index["hello"]["languages"], ["uk"])
        self.assertEqual(index["hello"]["values"]["uk"], "Привіт")
        # Single language → no parity gaps.
        self.assertEqual(index["hello"]["missing"], [])

    def test_two_languages_full_parity(self) -> None:
        _write(os.path.join(self.root, "locales", "uk", "common.json"), {"hello": "Привіт"})
        _write(os.path.join(self.root, "locales", "en", "common.json"), {"hello": "Hello"})
        index = locale_index.build_locale_index(self.root)
        entry = index["hello"]
        self.assertEqual(sorted(entry["languages"]), ["en", "uk"])
        self.assertEqual(entry["values"]["en"], "Hello")
        self.assertEqual(entry["values"]["uk"], "Привіт")
        self.assertEqual(entry["missing"], [])

    def test_missing_translation_listed_in_missing(self) -> None:
        """Both language dirs own ``common.json``; en lacks a key uk has.
        That's the parity-audit case the index has to surface."""
        _write(
            os.path.join(self.root, "locales", "uk", "common.json"),
            {"hello": "Привіт", "only_uk": "ТільКи_УК"},
        )
        _write(os.path.join(self.root, "locales", "en", "common.json"), {"hello": "Hello"})
        index = locale_index.build_locale_index(self.root)
        self.assertEqual(index["hello"]["missing"], [])
        self.assertEqual(index["only_uk"]["missing"], ["en"])

    def test_namespace_owners_drive_missing_calculation(self) -> None:
        """If a language doesn't own the namespace file at all, its
        keys are NOT counted as missing for that language — the
        ``missing`` list is namespace-scoped."""
        _write(os.path.join(self.root, "locales", "uk", "admin.json"), {"role_label": "Роль"})
        # en/ exists but only with common.json — no admin.json.
        _write(os.path.join(self.root, "locales", "en", "common.json"), {"hello": "Hello"})
        index = locale_index.build_locale_index(self.root)
        # role_label only lives in admin namespace; en doesn't own
        # admin.json, so 'en' is NOT listed as missing here — there's
        # nothing to be missing from.
        self.assertEqual(index["role_label"]["missing"], [])

    def test_non_string_values_skipped(self) -> None:
        """The index intentionally only catches str values — a project
        that stashes nested dicts in locale files keeps them, but they
        don't pollute the index."""
        _write(
            os.path.join(self.root, "locales", "uk", "common.json"),
            {
                "hello": "Привіт",
                "config": {"nested": "ignored"},
                "count": 5,
            },
        )
        index = locale_index.build_locale_index(self.root)
        self.assertIn("hello", index)
        self.assertNotIn("config", index)
        self.assertNotIn("count", index)

    def test_custom_locales_path(self) -> None:
        _write(os.path.join(self.root, "i18n", "uk", "common.json"), {"hi": "привіт"})
        index = locale_index.build_locale_index(self.root, locales_dir="i18n")
        self.assertIn("hi", index)


class WriteLocaleIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-locale-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_persists_sorted(self) -> None:
        index = {
            "z_key": {
                "namespace": "common",
                "languages": ["uk"],
                "values": {"uk": "z"},
                "missing": [],
            },
            "a_key": {
                "namespace": "common",
                "languages": ["uk"],
                "values": {"uk": "a"},
                "missing": [],
            },
        }
        out = locale_index.write_locale_index(self.root, index)
        self.assertTrue(os.path.exists(out))
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(list(data.keys()), ["a_key", "z_key"])


class QueryEngineLocaleTests(unittest.TestCase):
    """Engine surface — same lazy-load contract as every other artifact."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-locale-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _write_json(
            os.path.join(self.root, "agent_root.json"),
            {
                "project_root": self.root,
                "modules": ["."],
                "roles": {},
            },
        )
        _write_json(
            os.path.join(self.root, "agent_locale_keys.json"),
            {
                "staff_detail": {
                    "namespace": "admin",
                    "languages": ["en", "uk"],
                    "values": {"en": "Detail", "uk": "Деталі"},
                    "missing": [],
                },
                "common_btn_back": {
                    "namespace": "common",
                    "languages": ["uk"],
                    "values": {"uk": "Назад"},
                    "missing": ["en"],
                },
            },
        )

    def test_list_returns_all_keys_sorted(self) -> None:
        engine = QueryEngine(self.root)
        self.assertEqual(
            engine.list_locale_keys(),
            ["common_btn_back", "staff_detail"],
        )

    def test_list_filters_by_namespace(self) -> None:
        engine = QueryEngine(self.root)
        self.assertEqual(engine.list_locale_keys(namespace="admin"), ["staff_detail"])
        self.assertEqual(engine.list_locale_keys(namespace="common"), ["common_btn_back"])

    def test_find_substring_match(self) -> None:
        engine = QueryEngine(self.root)
        self.assertEqual(engine.find_locale_key("staff"), ["staff_detail"])
        self.assertEqual(engine.find_locale_key("BACK"), ["common_btn_back"])

    def test_find_empty_pattern_returns_empty(self) -> None:
        engine = QueryEngine(self.root)
        self.assertEqual(engine.find_locale_key(""), [])

    def test_get_returns_full_entry_or_none(self) -> None:
        engine = QueryEngine(self.root)
        entry = engine.get_locale_key("common_btn_back")
        self.assertEqual(entry["values"]["uk"], "Назад")
        self.assertEqual(entry["missing"], ["en"])
        self.assertIsNone(engine.get_locale_key("never_existed"))

    def test_missing_artifact_degrades_gracefully(self) -> None:
        """Project without a locales/ tree → empty list, not crash."""
        empty_root = tempfile.mkdtemp(prefix="vc-locale-")
        self.addCleanup(shutil.rmtree, empty_root, True)
        _write_json(
            os.path.join(empty_root, "agent_root.json"),
            {
                "project_root": empty_root,
                "modules": ["."],
                "roles": {},
            },
        )
        engine = QueryEngine(empty_root)
        self.assertEqual(engine.list_locale_keys(), [])
        self.assertIsNone(engine.get_locale_key("anything"))


if __name__ == "__main__":
    unittest.main()
