"""Tests for the file-level parse cache."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import parse_cache


def _write(path: str, content: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class LoadSaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-cache-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_missing_cache_returns_empty(self) -> None:
        cache = parse_cache.load(self.root)
        self.assertEqual(cache["entries"], {})
        # Epoch is computed from project state — non-empty hex.
        self.assertTrue(isinstance(cache["epoch"], str) and len(cache["epoch"]) > 0)
        self.assertEqual(cache["version"], parse_cache.CACHE_VERSION)

    def test_save_and_reload_roundtrip(self) -> None:
        cache = parse_cache.load(self.root)
        cache["entries"]["foo.py"] = {
            "mtime": 123.0,
            "size": 42,
            "result": {"exports": []},
        }
        parse_cache.save(self.root, cache)
        # Re-read.
        reloaded = parse_cache.load(self.root)
        self.assertEqual(reloaded["entries"], cache["entries"])

    def test_malformed_cache_returns_empty(self) -> None:
        path = os.path.join(self.root, parse_cache.CACHE_DIR, parse_cache.CACHE_FILENAME)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("{ not json")
        cache = parse_cache.load(self.root)
        self.assertEqual(cache["entries"], {})


class GetPutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-cache-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cache = parse_cache.load(self.root)

    def _make_file(self, rel_path: str, content: str = "x") -> str:
        abs_path = os.path.join(self.root, rel_path)
        _write(abs_path, content)
        return abs_path

    def test_get_miss_when_file_unknown(self) -> None:
        abs_path = self._make_file("a.py")
        self.assertIsNone(parse_cache.get(self.cache, "a.py", abs_path))

    def test_put_then_get_returns_payload(self) -> None:
        abs_path = self._make_file("a.py", "hello")
        payload = {"exports": [{"name": "X"}], "dependencies": []}
        parse_cache.put(self.cache, "a.py", abs_path, payload)
        got = parse_cache.get(self.cache, "a.py", abs_path)
        self.assertEqual(got, payload)

    def test_get_miss_when_size_changes(self) -> None:
        abs_path = self._make_file("a.py", "hello")
        parse_cache.put(self.cache, "a.py", abs_path, {"exports": []})
        # Mutate the file → size change → cache miss.
        _write(abs_path, "hello world!")
        self.assertIsNone(parse_cache.get(self.cache, "a.py", abs_path))

    def test_get_miss_when_mtime_changes(self) -> None:
        abs_path = self._make_file("a.py", "hello")
        parse_cache.put(self.cache, "a.py", abs_path, {"exports": []})
        # Bump mtime forward without changing content (touch). Size
        # stays the same; mtime differs → miss.
        future = time.time() + 60
        os.utime(abs_path, (future, future))
        self.assertIsNone(parse_cache.get(self.cache, "a.py", abs_path))

    def test_get_miss_when_file_deleted(self) -> None:
        abs_path = self._make_file("a.py")
        parse_cache.put(self.cache, "a.py", abs_path, {"exports": []})
        os.remove(abs_path)
        self.assertIsNone(parse_cache.get(self.cache, "a.py", abs_path))

    def test_put_no_op_when_file_missing(self) -> None:
        # Race: file was deleted between scan and put. put() should
        # silently no-op, not raise.
        parse_cache.put(
            self.cache, "ghost.py", os.path.join(self.root, "ghost.py"), {"exports": []}
        )
        self.assertNotIn("ghost.py", self.cache.get("entries", {}))


class EpochInvalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-cache-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _put_one_entry(self) -> None:
        cache = parse_cache.load(self.root)
        cache["entries"]["a.py"] = {"mtime": 1.0, "size": 1, "result": {}}
        parse_cache.save(self.root, cache)

    def test_no_conventions_change_keeps_cache(self) -> None:
        self._put_one_entry()
        reloaded = parse_cache.load(self.root)
        self.assertIn("a.py", reloaded["entries"])

    def test_conventions_change_invalidates(self) -> None:
        # Without any conventions file → epoch is fixed for an empty
        # config. Persist an entry. Then ADD a conventions file →
        # epoch changes → cache invalidates on next load.
        self._put_one_entry()
        conv_dir = os.path.join(self.root, parse_cache.CACHE_DIR)
        os.makedirs(conv_dir, exist_ok=True)
        with open(os.path.join(conv_dir, "conventions.json"), "w") as fh:
            json.dump({"some": "config"}, fh)
        reloaded = parse_cache.load(self.root)
        self.assertEqual(reloaded["entries"], {})

    def test_roles_change_invalidates(self) -> None:
        self._put_one_entry()
        conv_dir = os.path.join(self.root, parse_cache.CACHE_DIR)
        os.makedirs(conv_dir, exist_ok=True)
        with open(os.path.join(conv_dir, "roles.json"), "w") as fh:
            json.dump({"roles": []}, fh)
        reloaded = parse_cache.load(self.root)
        self.assertEqual(reloaded["entries"], {})


class PruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = {
            "entries": {
                "live1.py": {"mtime": 1.0, "size": 1, "result": {}},
                "live2.py": {"mtime": 2.0, "size": 2, "result": {}},
                "deleted.py": {"mtime": 3.0, "size": 3, "result": {}},
            }
        }

    def test_prune_keeps_live_drops_stale(self) -> None:
        parse_cache.prune(self.cache, {"live1.py", "live2.py"})
        self.assertEqual(set(self.cache["entries"]), {"live1.py", "live2.py"})

    def test_prune_handles_empty_live_set(self) -> None:
        parse_cache.prune(self.cache, set())
        # Everything stale → cache empties.
        self.assertEqual(self.cache["entries"], {})

    def test_prune_no_op_on_malformed_cache(self) -> None:
        cache = {"entries": "not a dict"}
        parse_cache.prune(cache, {"live1.py"})
        # Doesn't crash.
        self.assertEqual(cache["entries"], "not a dict")


if __name__ == "__main__":
    unittest.main()
