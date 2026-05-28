"""Tests for OllamaEmbeddingProvider and provider_from_conventions(ollama)."""

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

from semantic_store import (
    LocalHashEmbeddingProvider,
    OllamaEmbeddingProvider,
    provider_from_conventions,
)


def _write_json(path: str, payload: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class OllamaProviderInitTests(unittest.TestCase):
    """Instantiation and dim-detection logic — no network."""

    def test_defaults(self) -> None:
        p = OllamaEmbeddingProvider()
        self.assertEqual(p.name, "ollama")
        self.assertEqual(p.model, "nomic-embed-text")
        self.assertEqual(p.host, "http://localhost:11434")
        self.assertEqual(p.dim, 768)

    def test_known_dim_nomic(self) -> None:
        self.assertEqual(OllamaEmbeddingProvider("nomic-embed-text").dim, 768)

    def test_known_dim_mxbai(self) -> None:
        self.assertEqual(OllamaEmbeddingProvider("mxbai-embed-large").dim, 1024)

    def test_known_dim_all_minilm(self) -> None:
        self.assertEqual(OllamaEmbeddingProvider("all-minilm").dim, 384)

    def test_unknown_model_falls_back_to_768(self) -> None:
        p = OllamaEmbeddingProvider("some-unknown-model-v9")
        self.assertEqual(p.dim, 768)

    def test_custom_host_stripped(self) -> None:
        p = OllamaEmbeddingProvider(host="http://myhost:11434/")
        self.assertEqual(p.host, "http://myhost:11434")

    def test_custom_model(self) -> None:
        p = OllamaEmbeddingProvider(model="mxbai-embed-large", host="http://localhost:11434")
        self.assertEqual(p.model, "mxbai-embed-large")
        self.assertEqual(p.dim, 1024)


class OllamaProviderEmbedTests(unittest.TestCase):
    """embed() — mocked HTTP, no actual Ollama required."""

    def _mock_response(self, embedding: list) -> mock.MagicMock:
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps({"embedding": embedding}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = mock.MagicMock(return_value=False)
        return resp

    def test_embed_returns_floats(self) -> None:
        vec = [0.1] * 768
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(vec)):
            p = OllamaEmbeddingProvider()
            result = p.embed("hello world")
        self.assertEqual(len(result), 768)
        self.assertAlmostEqual(result[0], 0.1)

    def test_embed_updates_dim_for_unknown_model(self) -> None:
        vec = [0.0] * 1024
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(vec)):
            p = OllamaEmbeddingProvider(model="some-unknown-1024-model")
            p.embed("test")
        self.assertEqual(p.dim, 1024)

    def test_embed_raises_runtime_error_when_server_down(self) -> None:
        import urllib.error

        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            p = OllamaEmbeddingProvider()
            with self.assertRaises(RuntimeError) as ctx:
                p.embed("hello")
        self.assertIn("ollama serve", str(ctx.exception))
        self.assertIn("nomic-embed-text", str(ctx.exception))

    def test_embed_sends_correct_payload(self) -> None:
        captured = {}
        vec = [0.5] * 768

        def fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data)
            captured["url"] = req.full_url
            return self._mock_response(vec)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            p = OllamaEmbeddingProvider(model="mxbai-embed-large")
            p.embed("my symbol text")

        self.assertEqual(captured["data"]["model"], "mxbai-embed-large")
        self.assertEqual(captured["data"]["prompt"], "my symbol text")
        self.assertIn("/api/embeddings", captured["url"])


class ProviderFromConventionsOllamaTests(unittest.TestCase):
    """provider_from_conventions correctly returns OllamaEmbeddingProvider."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-ollama-conv-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _write_conventions(self, cfg: object) -> None:
        _write_json(os.path.join(self.root, ".vc-context", "conventions.json"), cfg)

    def test_string_form(self) -> None:
        self._write_conventions({"embedding_provider": "ollama"})
        p = provider_from_conventions(self.root)
        self.assertIsInstance(p, OllamaEmbeddingProvider)
        self.assertEqual(p.model, OllamaEmbeddingProvider.DEFAULT_MODEL)
        self.assertEqual(p.host, OllamaEmbeddingProvider.DEFAULT_HOST)

    def test_dict_form_with_model(self) -> None:
        self._write_conventions({
            "embedding_provider": {
                "name": "ollama",
                "model": "mxbai-embed-large",
            }
        })
        p = provider_from_conventions(self.root)
        self.assertIsInstance(p, OllamaEmbeddingProvider)
        self.assertEqual(p.model, "mxbai-embed-large")
        self.assertEqual(p.dim, 1024)

    def test_dict_form_with_host(self) -> None:
        self._write_conventions({
            "embedding_provider": {
                "name": "ollama",
                "host": "http://gpu-server:11434",
            }
        })
        p = provider_from_conventions(self.root)
        self.assertIsInstance(p, OllamaEmbeddingProvider)
        self.assertEqual(p.host, "http://gpu-server:11434")

    def test_dict_form_model_and_host(self) -> None:
        self._write_conventions({
            "embedding_provider": {
                "name": "ollama",
                "model": "bge-m3",
                "host": "http://remote:11434",
            }
        })
        p = provider_from_conventions(self.root)
        self.assertIsInstance(p, OllamaEmbeddingProvider)
        self.assertEqual(p.model, "bge-m3")
        self.assertEqual(p.host, "http://remote:11434")
        self.assertEqual(p.dim, 1024)

    def test_missing_conventions_falls_back_to_local_hash(self) -> None:
        p = provider_from_conventions(self.root)
        self.assertIsInstance(p, LocalHashEmbeddingProvider)

    def test_unknown_provider_falls_back_to_local_hash(self) -> None:
        self._write_conventions({"embedding_provider": "unknown_provider_xyz"})
        p = provider_from_conventions(self.root)
        self.assertIsInstance(p, LocalHashEmbeddingProvider)


class AutoReindexOllamaTests(unittest.TestCase):
    """auto_reindex._build_cmd does not add --with for ollama provider."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-ar-ollama-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _set_provider(self, name: str) -> None:
        _write_json(
            os.path.join(self.root, ".vc-context", "conventions.json"),
            {"embedding_provider": {"name": name}},
        )

    def test_ollama_no_uv_with(self) -> None:
        import auto_reindex

        self._set_provider("ollama")
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0  # uv --version succeeds
            cmd = auto_reindex._build_cmd("agent_map.py", self.root)
        # ollama has no entry in _PROVIDER_PACKAGES → no --with added
        self.assertNotIn("--with", cmd)

    def test_sentence_transformers_adds_uv_with(self) -> None:
        import auto_reindex

        self._set_provider("sentence_transformers")
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            cmd = auto_reindex._build_cmd("agent_map.py", self.root)
        self.assertIn("--with", cmd)
        self.assertTrue(any("sentence" in c for c in cmd))


if __name__ == "__main__":
    unittest.main()
