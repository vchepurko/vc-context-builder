"""Tests for LLM-based anti-pattern detection."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

from anti_patterns import (
    detect_with_llm,
    has_static_rule,
    load_llm_rules,
    _extract_chunks,
    _files_for_scope,
)
from query_engine import QueryEngine


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(content).lstrip())


def _write_conventions(root: str, cfg: object) -> None:
    p = os.path.join(root, ".vc-context", "conventions.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        json.dump(cfg, fh)


class LoadLlmRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-llmrules-")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_no_conventions_returns_empty(self) -> None:
        self.assertEqual(load_llm_rules(self.root), [])

    def test_missing_anti_patterns_key_returns_empty(self) -> None:
        _write_conventions(self.root, {"embedding_provider": "local_hash"})
        self.assertEqual(load_llm_rules(self.root), [])

    def test_returns_valid_rules(self) -> None:
        _write_conventions(
            self.root,
            {
                "anti_patterns": [
                    {"name": "raw-sql-in-view", "description": "SQL in views", "scope": "views/**/*.py"},
                    {"name": "logic-in-serializer", "description": "Business logic in serializers"},
                ]
            },
        )
        rules = load_llm_rules(self.root)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]["name"], "raw-sql-in-view")
        self.assertEqual(rules[1]["scope"] if "scope" in rules[1] else "default", "default")

    def test_filters_rules_without_name_or_description(self) -> None:
        _write_conventions(
            self.root,
            {
                "anti_patterns": [
                    {"name": "valid", "description": "A valid rule"},
                    {"description": "No name"},
                    {"name": "no-description"},
                    "not-a-dict",
                ]
            },
        )
        rules = load_llm_rules(self.root)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["name"], "valid")

    def test_corrupt_json_returns_empty(self) -> None:
        p = os.path.join(self.root, ".vc-context", "conventions.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write("{INVALID")
        self.assertEqual(load_llm_rules(self.root), [])


class HasStaticRuleTests(unittest.TestCase):
    def test_known_rule(self) -> None:
        self.assertTrue(has_static_rule("aiogram-state-check-in-body"))

    def test_unknown_rule(self) -> None:
        self.assertFalse(has_static_rule("raw-sql-in-view"))


class ExtractChunksTests(unittest.TestCase):
    def test_extracts_top_level_functions(self) -> None:
        source = textwrap.dedent("""\
            def foo():
                pass

            def bar():
                return 1
        """)
        import ast
        tree = ast.parse(source)
        chunks = _extract_chunks(source, tree)
        names = [c[0] for c in chunks]
        self.assertIn("foo", names)
        self.assertIn("bar", names)

    def test_extracts_class_methods(self) -> None:
        source = textwrap.dedent("""\
            class MyView:
                def get(self, request):
                    pass

                def post(self, request):
                    pass
        """)
        import ast
        tree = ast.parse(source)
        chunks = _extract_chunks(source, tree)
        names = [c[0] for c in chunks]
        self.assertIn("MyView.get", names)
        self.assertIn("MyView.post", names)

    def test_large_function_truncated(self) -> None:
        many_lines = "\n".join(f"    x = {i}" for i in range(150))
        source = f"def big():\n{many_lines}\n"
        import ast
        tree = ast.parse(source)
        chunks = _extract_chunks(source, tree)
        self.assertEqual(len(chunks), 1)
        chunk_source = chunks[0][1]
        self.assertIn("truncated", chunk_source)
        self.assertLessEqual(len(chunk_source.splitlines()), 110)

    def test_returns_start_line(self) -> None:
        source = textwrap.dedent("""\
            x = 1

            def later():
                pass
        """)
        import ast
        tree = ast.parse(source)
        chunks = _extract_chunks(source, tree)
        self.assertEqual(len(chunks), 1)
        self.assertGreater(chunks[0][2], 1)  # starts after line 1


class FilesForScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-scope-")
        # Create some files
        _write(os.path.join(self.root, "views", "main.py"), "x = 1\n")
        _write(os.path.join(self.root, "services", "user.py"), "x = 1\n")
        _write(os.path.join(self.root, "venv", "lib.py"), "x = 1\n")  # should be ignored
        _write(os.path.join(self.root, "data.js"), "x = 1\n")  # not .py

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_glob_star_star_finds_python_files(self) -> None:
        files = _files_for_scope(self.root, "**/*.py")
        rels = [os.path.relpath(f, self.root).replace(os.sep, "/") for f in files]
        self.assertIn("views/main.py", rels)
        self.assertIn("services/user.py", rels)

    def test_ignores_venv_dir(self) -> None:
        files = _files_for_scope(self.root, "**/*.py")
        rels = [os.path.relpath(f, self.root).replace(os.sep, "/") for f in files]
        self.assertNotIn("venv/lib.py", rels)

    def test_non_python_files_excluded(self) -> None:
        files = _files_for_scope(self.root, "**/*.py")
        self.assertFalse(any(f.endswith(".js") for f in files))

    def test_scoped_glob_limits_results(self) -> None:
        files = _files_for_scope(self.root, "views/**/*.py")
        rels = [os.path.relpath(f, self.root).replace(os.sep, "/") for f in files]
        self.assertIn("views/main.py", rels)
        self.assertNotIn("services/user.py", rels)


class DetectWithLlmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-detect-llm-")
        _write(
            os.path.join(self.root, "views", "main.py"),
            """
            def get_user(request):
                result = db.execute("SELECT * FROM users WHERE id = %s", [request.id])
                return result
            """,
        )
        _write(
            os.path.join(self.root, "services", "clean.py"),
            """
            def get_user(user_id):
                return User.objects.get(id=user_id)
            """,
        )
        self.rule_def = {
            "name": "raw-sql-in-view",
            "description": "Direct SQL queries in view functions",
            "scope": "**/*.py",
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _make_chat(self, responses: dict) -> mock.MagicMock:
        """Returns a mock chat whose generate() replies by file path."""
        chat = mock.MagicMock()

        def generate(prompt, **kw):
            for key, resp in responses.items():
                if key in prompt:
                    return resp
            return "NO"

        chat.generate.side_effect = generate
        return chat

    def test_returns_hits_for_matching_functions(self) -> None:
        chat = self._make_chat({"SELECT * FROM users": "YES: raw SQL query found"})
        cache: dict = {}
        hits = detect_with_llm(self.root, self.rule_def, chat, cache)
        files_hit = {h["file"] for h in hits}
        self.assertIn("views/main.py", files_hit)

    def test_clean_file_produces_no_hits(self) -> None:
        chat = self._make_chat({"SELECT * FROM users": "YES: raw SQL found"})
        cache: dict = {}
        hits = detect_with_llm(self.root, self.rule_def, chat, cache)
        files_hit = {h["file"] for h in hits}
        self.assertNotIn("services/clean.py", files_hit)

    def test_hit_record_shape(self) -> None:
        chat = self._make_chat({"SELECT * FROM users": "YES: raw SQL found"})
        cache: dict = {}
        hits = detect_with_llm(self.root, self.rule_def, chat, cache)
        hit = next(h for h in hits if h["file"] == "views/main.py")
        self.assertEqual(hit["rule"], "raw-sql-in-view")
        self.assertIsInstance(hit["line"], int)
        self.assertIn("function", hit)
        self.assertIn("evidence", hit)

    def test_evidence_extracted_from_yes_response(self) -> None:
        chat = self._make_chat({"SELECT * FROM users": "YES: uses db.execute directly"})
        cache: dict = {}
        hits = detect_with_llm(self.root, self.rule_def, chat, cache)
        hit = next(h for h in hits if h["file"] == "views/main.py")
        self.assertEqual(hit["evidence"], "uses db.execute directly")

    def test_cache_prevents_second_llm_call(self) -> None:
        chat = mock.MagicMock()
        chat.generate.return_value = "YES: found"
        cache: dict = {}
        detect_with_llm(self.root, self.rule_def, chat, cache)
        first_call_count = chat.generate.call_count
        detect_with_llm(self.root, self.rule_def, chat, cache)
        self.assertEqual(chat.generate.call_count, first_call_count)

    def test_scoped_glob_limits_files_scanned(self) -> None:
        scoped_rule = {**self.rule_def, "scope": "views/**/*.py"}
        chat = mock.MagicMock()
        chat.generate.return_value = "NO"
        cache: dict = {}
        detect_with_llm(self.root, scoped_rule, chat, cache)
        # Only files in views/ should be checked
        scanned = [key[1] for key in cache]
        self.assertTrue(all("views" in p for p in scanned))

    def test_generate_exception_skips_chunk(self) -> None:
        chat = mock.MagicMock()
        chat.generate.side_effect = RuntimeError("server down")
        cache: dict = {}
        hits = detect_with_llm(self.root, self.rule_def, chat, cache)
        self.assertEqual(hits, [])

    def test_max_chunks_per_file_respected(self) -> None:
        # Write a file with many functions
        many_funcs = "\n".join(f"def func_{i}():\n    pass\n" for i in range(30))
        _write(os.path.join(self.root, "big_module.py"), many_funcs)
        chat = mock.MagicMock()
        chat.generate.return_value = "NO"
        cache: dict = {}
        detect_with_llm(self.root, self.rule_def, chat, cache, max_chunks_per_file=5)
        # big_module.py has 30 funcs but only 5 should be scanned
        calls_for_big = sum(
            1 for call in chat.generate.call_args_list
            if "func_" in str(call)
        )
        self.assertLessEqual(calls_for_big, 5)


class QueryEngineLlmAntiPatternsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-engine-anti-")
        _write_conventions(
            self.root,
            {
                "chat_provider": {"name": "ollama", "model": "qwen2.5-coder:1.5b"},
                "anti_patterns": [
                    {
                        "name": "raw-sql-in-view",
                        "description": "Direct SQL queries in view functions",
                        "scope": "**/*.py",
                    }
                ],
            },
        )
        _write(
            os.path.join(self.root, "views.py"),
            """
            def get(request):
                return db.execute("SELECT 1")
            """,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_list_includes_static_and_llm_rules(self) -> None:
        engine = QueryEngine(self.root)
        rules = engine.list_anti_patterns()
        self.assertIn("aiogram-state-check-in-body", rules)
        self.assertIn("raw-sql-in-view", rules)

    def test_static_rule_still_works(self) -> None:
        engine = QueryEngine(self.root)
        # Unknown static rule — should return []
        self.assertEqual(engine.find_anti_patterns("does-not-exist"), [])

    def test_llm_rule_returns_hits_with_mock_chat(self) -> None:
        engine = QueryEngine(self.root)
        engine._llm_antipattern_cache.clear()

        with mock.patch(
            "ollama_chat.OllamaChatProvider.generate",
            return_value="YES: raw SQL detected",
        ):
            hits = engine.find_anti_patterns("raw-sql-in-view")

        self.assertIsInstance(hits, list)
        self.assertTrue(len(hits) > 0)
        self.assertEqual(hits[0]["rule"], "raw-sql-in-view")

    def test_llm_rule_no_chat_provider_returns_empty(self) -> None:
        # Remove chat_provider from conventions
        _write_conventions(
            self.root,
            {
                "anti_patterns": [
                    {"name": "raw-sql-in-view", "description": "SQL in views"}
                ]
            },
        )
        engine = QueryEngine(self.root)
        engine._llm_antipattern_cache.clear()
        hits = engine.find_anti_patterns("raw-sql-in-view")
        self.assertEqual(hits, [])

    def test_llm_rule_server_down_returns_empty(self) -> None:
        import urllib.error

        engine = QueryEngine(self.root)
        engine._llm_antipattern_cache.clear()

        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            hits = engine.find_anti_patterns("raw-sql-in-view")

        self.assertEqual(hits, [])

    def test_unknown_llm_rule_returns_empty(self) -> None:
        engine = QueryEngine(self.root)
        hits = engine.find_anti_patterns("no-such-custom-rule")
        self.assertEqual(hits, [])

    def test_session_cache_prevents_duplicate_scans(self) -> None:
        engine = QueryEngine(self.root)
        engine._llm_antipattern_cache.clear()

        call_count = {"n": 0}

        def counting_generate(prompt, **kw):
            call_count["n"] += 1
            return "NO"

        with mock.patch("ollama_chat.OllamaChatProvider.generate", counting_generate):
            engine.find_anti_patterns("raw-sql-in-view")
            first = call_count["n"]
            engine.find_anti_patterns("raw-sql-in-view")
            second = call_count["n"]

        self.assertEqual(first, second)  # no extra calls on second run


if __name__ == "__main__":
    unittest.main()
