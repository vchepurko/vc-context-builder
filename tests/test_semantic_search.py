"""Tests for Phase 5 semantic symbol search."""

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

from mcp.dispatcher import Dispatcher
from mcp_server import _tool_specs
from paths import local_state_dir
from query_engine import QueryEngine
from semantic_store import build_symbol_store, db_path, semantic_search


def _write_json(path: str, payload: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class SemanticSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-semantic-")
        self.home = tempfile.mkdtemp(prefix="vc-semantic-home-")
        self.old_home = os.environ.get("VC_CONTEXT_HOME")
        os.environ["VC_CONTEXT_HOME"] = self.home
        self.symbols = {
            "ScormCompletionService": {
                "file": "src/app/course/scorm-completion.service.ts",
                "line": 12,
                "kind": "class",
                "role": "service",
                "doc": "Handles SCORM course completion and progress sync.",
                "params": "()",
            },
            "PaymentWebhook": {
                "file": "backend/payments/webhooks.py",
                "line": 5,
                "kind": "func",
                "role": "webhook",
                "doc": "Receives payment provider callbacks.",
            },
            "ScormCompletionFixture": {
                "file": "tests/scorm/test_completion.py",
                "line": 7,
                "kind": "func",
                "doc": "Fixture for SCORM completion tests.",
            },
        }
        _write_json(os.path.join(self.root, "agent_symbols.json"), self.symbols)

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("VC_CONTEXT_HOME", None)
        else:
            os.environ["VC_CONTEXT_HOME"] = self.old_home
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_build_writes_per_repo_sqlite_store(self) -> None:
        result = build_symbol_store(self.root, self.symbols)
        self.assertEqual(result["symbols"], 3)
        self.assertEqual(result["provider"], "local_hash")
        self.assertTrue(os.path.exists(db_path(self.root)))
        self.assertTrue(db_path(self.root).startswith(local_state_dir(self.root)))

    def test_semantic_search_finds_concept_without_exact_name(self) -> None:
        hits = semantic_search(self.root, self.symbols, "course progress completion", top_k=2)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["name"], "ScormCompletionService")
        self.assertIn("doc", hits[0]["why"])

    def test_semantic_search_filters_tests_by_default(self) -> None:
        hits = semantic_search(self.root, self.symbols, "completion fixture", top_k=5)
        names = [h["name"] for h in hits]
        self.assertNotIn("ScormCompletionFixture", names)
        hits_with_tests = semantic_search(
            self.root,
            self.symbols,
            "completion fixture",
            top_k=5,
            include_tests=True,
        )
        self.assertIn("ScormCompletionFixture", [h["name"] for h in hits_with_tests])

    def test_query_engine_and_dispatcher_wiring(self) -> None:
        engine = QueryEngine(self.root)
        hits = engine.semantic_search("payment callback", top_k=1)
        self.assertEqual(hits[0]["name"], "PaymentWebhook")

        out = Dispatcher(engine).call(
            "semantic_search",
            {"query": "course completion", "top_k": 1, "kind": "class"},
        )
        self.assertEqual(out[0]["name"], "ScormCompletionService")

    def test_tool_spec_lists_semantic_search(self) -> None:
        spec = next(s for s in _tool_specs() if s["name"] == "semantic_search")
        self.assertEqual(spec["inputSchema"].get("required"), ["query"])


if __name__ == "__main__":
    unittest.main()
