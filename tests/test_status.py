"""Tests for the status command (get_status callable)."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

from status import get_status


def _write_json(path: str, payload: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class GetStatusIndexTests(unittest.TestCase):
    """index section of get_status."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-status-")
        self.home = tempfile.mkdtemp(prefix="vc-status-home-")
        self._old_home = os.environ.get("VC_CONTEXT_HOME")
        os.environ["VC_CONTEXT_HOME"] = self.home
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.home, True)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("VC_CONTEXT_HOME", None)
        else:
            os.environ["VC_CONTEXT_HOME"] = self._old_home

    def test_no_index_returns_not_exists(self) -> None:
        result = get_status(self.root)
        idx = result["index"]
        self.assertFalse(idx["exists"])
        self.assertIsNone(idx["last_built"])
        self.assertIsNone(idx["age_seconds"])
        self.assertTrue(idx["stale"])
        self.assertEqual(idx["symbols_count"], 0)

    def test_index_exists_returns_age(self) -> None:
        # Write a fresh agent_root.json
        root_path = os.path.join(self.root, ".vc-context", "index", "agent_root.json")
        _write_json(root_path, {"modules": []})
        # Fake mtime to 10 seconds ago
        now = time.time()
        os.utime(root_path, (now - 10, now - 10))

        result = get_status(self.root)
        idx = result["index"]
        self.assertTrue(idx["exists"])
        self.assertIsNotNone(idx["last_built"])
        self.assertGreaterEqual(idx["age_seconds"], 9.0)

    def test_symbols_count_from_agent_symbols(self) -> None:
        root_path = os.path.join(self.root, ".vc-context", "index", "agent_root.json")
        _write_json(root_path, {})
        sym_path = os.path.join(self.root, ".vc-context", "index", "agent_symbols.json")
        _write_json(sym_path, {"A": {}, "B": {}, "C": {}})

        result = get_status(self.root)
        self.assertEqual(result["index"]["symbols_count"], 3)

    def test_auto_reindex_config_present(self) -> None:
        _write_json(
            os.path.join(self.root, ".vc-context", "conventions.json"),
            {"auto_reindex": {"enabled": True, "interval_seconds": 900}},
        )
        result = get_status(self.root)
        ar = result["index"]["auto_reindex"]
        self.assertTrue(ar["enabled"])
        self.assertEqual(ar["interval_seconds"], 900)

    def test_auto_reindex_disabled_by_default(self) -> None:
        result = get_status(self.root)
        ar = result["index"]["auto_reindex"]
        self.assertFalse(ar["enabled"])

    def test_project_root_is_absolute(self) -> None:
        result = get_status(self.root)
        self.assertTrue(os.path.isabs(result["project_root"]))


class GetStatusEmbeddingsTests(unittest.TestCase):
    """embeddings section of get_status."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-status-emb-")
        self.home = tempfile.mkdtemp(prefix="vc-status-emb-home-")
        self._old_home = os.environ.get("VC_CONTEXT_HOME")
        os.environ["VC_CONTEXT_HOME"] = self.home
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.home, True)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("VC_CONTEXT_HOME", None)
        else:
            os.environ["VC_CONTEXT_HOME"] = self._old_home

    def _write_conventions(self, cfg: object) -> None:
        _write_json(os.path.join(self.root, ".vc-context", "conventions.json"), cfg)

    def test_local_hash_provider_no_model(self) -> None:
        result = get_status(self.root)
        emb = result["embeddings"]
        self.assertEqual(emb["provider"], "local_hash")
        self.assertIsNone(emb["model"])

    def test_ollama_provider_shows_model(self) -> None:
        self._write_conventions(
            {"embedding_provider": {"name": "ollama", "model": "mxbai-embed-large"}}
        )
        result = get_status(self.root)
        emb = result["embeddings"]
        self.assertEqual(emb["provider"], "ollama")
        self.assertEqual(emb["model"], "mxbai-embed-large")

    def test_sqlite_not_exists_when_not_built(self) -> None:
        result = get_status(self.root)
        emb = result["embeddings"]
        self.assertFalse(emb["sqlite_exists"])
        self.assertIsNone(emb["sqlite_size_bytes"])
        self.assertIsNone(emb["symbols_indexed"])

    def test_sqlite_exists_reports_size_and_count(self) -> None:
        from paths import ensure_local_state_dir
        from stores.semantic_store import DB_FILENAME

        db_dir = ensure_local_state_dir(self.root, "embeddings")
        db_file = os.path.join(db_dir, DB_FILENAME)

        # Create a minimal SQLite with the symbols table
        conn = sqlite3.connect(db_file)
        conn.execute(
            "CREATE TABLE symbols (name TEXT PRIMARY KEY, vector TEXT, file TEXT, "
            "line INTEGER, kind TEXT, role TEXT, doc TEXT)"
        )
        conn.execute("INSERT INTO symbols VALUES ('Foo','[]','a.py',1,'func',NULL,NULL)")
        conn.execute("INSERT INTO symbols VALUES ('Bar','[]','b.py',2,'class',NULL,NULL)")
        conn.commit()
        conn.close()

        result = get_status(self.root)
        emb = result["embeddings"]
        self.assertTrue(emb["sqlite_exists"])
        self.assertGreater(emb["sqlite_size_bytes"], 0)
        self.assertEqual(emb["symbols_indexed"], 2)

    def test_status_keys_always_present(self) -> None:
        result = get_status(self.root)
        self.assertIn("project_root", result)
        self.assertIn("index", result)
        self.assertIn("embeddings", result)
        for key in (
            "exists",
            "last_built",
            "age_seconds",
            "stale",
            "symbols_count",
            "auto_reindex",
        ):
            self.assertIn(key, result["index"], f"missing index.{key}")
        for key in ("provider", "model", "sqlite_exists", "sqlite_size_bytes", "symbols_indexed"):
            self.assertIn(key, result["embeddings"], f"missing embeddings.{key}")


class GetStatusErrorHandlingTests(unittest.TestCase):
    """get_status never raises — errors surface in the result dict."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-status-err-")
        self.home = tempfile.mkdtemp(prefix="vc-status-err-home-")
        self._old_home = os.environ.get("VC_CONTEXT_HOME")
        os.environ["VC_CONTEXT_HOME"] = self.home
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.home, True)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("VC_CONTEXT_HOME", None)
        else:
            os.environ["VC_CONTEXT_HOME"] = self._old_home

    def test_corrupt_conventions_json_does_not_raise(self) -> None:
        conv = os.path.join(self.root, ".vc-context", "conventions.json")
        os.makedirs(os.path.dirname(conv), exist_ok=True)
        with open(conv, "w") as fh:
            fh.write("{INVALID JSON")
        result = get_status(self.root)
        # Should still return a valid dict without raising
        self.assertIn("index", result)
        self.assertIn("embeddings", result)

    def test_returns_dict_for_fresh_empty_project(self) -> None:
        result = get_status(self.root)
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result["index"], dict)
        self.assertIsInstance(result["embeddings"], dict)


if __name__ == "__main__":
    unittest.main()
