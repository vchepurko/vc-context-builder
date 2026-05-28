"""Tests for OllamaChatProvider and chat_provider_from_conventions."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

from ollama_chat import OllamaChatProvider, chat_provider_from_conventions


def _mock_response(text: str) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps({"response": text}).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


class OllamaChatProviderInitTests(unittest.TestCase):
    def test_defaults(self) -> None:
        p = OllamaChatProvider()
        self.assertEqual(p.model, OllamaChatProvider.DEFAULT_MODEL)
        self.assertEqual(p.host, OllamaChatProvider.DEFAULT_HOST)

    def test_custom_model_and_host(self) -> None:
        p = OllamaChatProvider(model="llama3.2:3b", host="http://gpu:11434/")
        self.assertEqual(p.model, "llama3.2:3b")
        self.assertEqual(p.host, "http://gpu:11434")  # trailing slash stripped

    def test_trailing_slash_stripped(self) -> None:
        p = OllamaChatProvider(host="http://localhost:11434/")
        self.assertEqual(p.host, "http://localhost:11434")


class OllamaChatGenerateTests(unittest.TestCase):
    def test_returns_response_text(self) -> None:
        with mock.patch(
            "urllib.request.urlopen", return_value=_mock_response("Hello world")
        ):
            p = OllamaChatProvider()
            result = p.generate("say hello")
        self.assertEqual(result, "Hello world")

    def test_strips_whitespace(self) -> None:
        with mock.patch(
            "urllib.request.urlopen", return_value=_mock_response("  trimmed  \n")
        ):
            result = OllamaChatProvider().generate("prompt")
        self.assertEqual(result, "trimmed")

    def test_sends_correct_payload(self) -> None:
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data)
            captured["url"] = req.full_url
            return _mock_response("ok")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            OllamaChatProvider(model="qwen2.5-coder:1.5b").generate(
                "describe this", system="be brief"
            )

        self.assertEqual(captured["data"]["model"], "qwen2.5-coder:1.5b")
        self.assertEqual(captured["data"]["prompt"], "describe this")
        self.assertEqual(captured["data"]["system"], "be brief")
        self.assertFalse(captured["data"]["stream"])
        self.assertIn("/api/generate", captured["url"])

    def test_no_system_omits_key(self) -> None:
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data)
            return _mock_response("ok")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            OllamaChatProvider().generate("no system")
        self.assertNotIn("system", captured["data"])

    def test_server_down_raises_runtime_error(self) -> None:
        import urllib.error

        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                OllamaChatProvider().generate("test")
        self.assertIn("ollama serve", str(ctx.exception))

    def test_unknown_model_raises_runtime_error(self) -> None:
        # Simulate Ollama returning an error JSON (model not found)
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps({"error": "model not found"}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = mock.MagicMock(return_value=False)
        # When 'response' key is missing, generate returns "" (no error)
        with mock.patch("urllib.request.urlopen", return_value=resp):
            result = OllamaChatProvider().generate("test")
        self.assertEqual(result, "")


class ChatProviderFromConventionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-chat-conv-")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_conventions(self, cfg: object) -> None:
        p = os.path.join(self.root, ".vc-context", "conventions.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            json.dump(cfg, fh)

    def test_no_conventions_returns_none(self) -> None:
        self.assertIsNone(chat_provider_from_conventions(self.root))

    def test_missing_chat_provider_key_returns_none(self) -> None:
        self._write_conventions({"embedding_provider": "local_hash"})
        self.assertIsNone(chat_provider_from_conventions(self.root))

    def test_string_form_ollama(self) -> None:
        self._write_conventions({"chat_provider": "ollama"})
        p = chat_provider_from_conventions(self.root)
        self.assertIsNotNone(p)
        self.assertIsInstance(p, OllamaChatProvider)
        self.assertEqual(p.model, OllamaChatProvider.DEFAULT_MODEL)  # type: ignore[union-attr]

    def test_string_form_unknown_returns_none(self) -> None:
        self._write_conventions({"chat_provider": "openai"})
        self.assertIsNone(chat_provider_from_conventions(self.root))

    def test_dict_form_with_model(self) -> None:
        self._write_conventions({"chat_provider": {"name": "ollama", "model": "llama3.2:3b"}})
        p = chat_provider_from_conventions(self.root)
        self.assertIsNotNone(p)
        self.assertEqual(p.model, "llama3.2:3b")  # type: ignore[union-attr]

    def test_dict_form_with_host(self) -> None:
        self._write_conventions(
            {"chat_provider": {"name": "ollama", "host": "http://gpu-box:11434"}}
        )
        p = chat_provider_from_conventions(self.root)
        self.assertIsNotNone(p)
        self.assertEqual(p.host, "http://gpu-box:11434")  # type: ignore[union-attr]

    def test_dict_form_model_and_host(self) -> None:
        self._write_conventions(
            {
                "chat_provider": {
                    "name": "ollama",
                    "model": "codellama:7b",
                    "host": "http://remote:11434",
                }
            }
        )
        p = chat_provider_from_conventions(self.root)
        self.assertIsNotNone(p)
        self.assertEqual(p.model, "codellama:7b")  # type: ignore[union-attr]
        self.assertEqual(p.host, "http://remote:11434")  # type: ignore[union-attr]

    def test_corrupt_json_returns_none(self) -> None:
        p = os.path.join(self.root, ".vc-context", "conventions.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write("{INVALID")
        self.assertIsNone(chat_provider_from_conventions(self.root))


class SummariseModuleWithChatTests(unittest.TestCase):
    """summarise_module adds 'summary' when chat provider is configured."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-summod-")
        self.home = tempfile.mkdtemp(prefix="vc-summod-home-")
        self._old_home = os.environ.get("VC_CONTEXT_HOME")
        os.environ["VC_CONTEXT_HOME"] = self.home
        # Write a minimal conventions.json with chat_provider
        conv = os.path.join(self.root, ".vc-context", "conventions.json")
        os.makedirs(os.path.dirname(conv), exist_ok=True)
        with open(conv, "w") as fh:
            json.dump({"chat_provider": {"name": "ollama", "model": "qwen2.5-coder:1.5b"}}, fh)
        # Write a minimal _module_map.json
        mod_dir = os.path.join(self.root, "business_logic", "users")
        os.makedirs(mod_dir, exist_ok=True)
        module_map = {
            "directory": "business_logic/users",
            "files": {
                "services.py": {
                    "exports": [
                        {"name": "get_user", "kind": "func", "role": "service",
                         "doc": "Return a user by ID."},
                        {"name": "create_user", "kind": "func", "role": "service",
                         "doc": "Create a new user record."},
                    ],
                    "dependencies": ["business_logic/core/"],
                }
            },
        }
        with open(os.path.join(mod_dir, "_module_map.json"), "w") as fh:
            json.dump(module_map, fh)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("VC_CONTEXT_HOME", None)
        else:
            os.environ["VC_CONTEXT_HOME"] = self._old_home
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_summary_added_when_chat_available(self) -> None:
        from query_engine import QueryEngine

        with mock.patch(
            "urllib.request.urlopen",
            return_value=_mock_response("Handles user CRUD operations."),
        ):
            engine = QueryEngine(self.root)
            engine._summary_cache.clear()
            result = engine.summarise_module("business_logic/users")

        self.assertIsNotNone(result)
        self.assertIn("summary", result)
        self.assertEqual(result["summary"], "Handles user CRUD operations.")

    def test_summary_cached_on_second_call(self) -> None:
        from query_engine import QueryEngine

        call_count = {"n": 0}
        orig = OllamaChatProvider.generate

        def counting_generate(self, prompt, **kw):
            call_count["n"] += 1
            return "Cached description."

        with mock.patch.object(OllamaChatProvider, "generate", counting_generate):
            engine = QueryEngine(self.root)
            engine._summary_cache.clear()
            engine.summarise_module("business_logic/users")
            engine.summarise_module("business_logic/users")

        self.assertEqual(call_count["n"], 1)  # only one LLM call

    def test_server_down_returns_result_without_summary(self) -> None:
        import urllib.error
        from query_engine import QueryEngine

        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            engine = QueryEngine(self.root)
            engine._summary_cache.clear()
            result = engine.summarise_module("business_logic/users")

        self.assertIsNotNone(result)
        self.assertNotIn("summary", result)

    def test_no_chat_provider_returns_result_without_summary(self) -> None:
        # Remove chat_provider from conventions
        conv = os.path.join(self.root, ".vc-context", "conventions.json")
        with open(conv, "w") as fh:
            json.dump({}, fh)
        from query_engine import QueryEngine

        engine = QueryEngine(self.root)
        engine._summary_cache.clear()
        result = engine.summarise_module("business_logic/users")
        self.assertIsNotNone(result)
        self.assertNotIn("summary", result)


if __name__ == "__main__":
    unittest.main()
