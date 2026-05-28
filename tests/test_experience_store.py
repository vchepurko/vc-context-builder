"""Tests for Phase 5 local experience store."""

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

from stores.experience_store import db_path, recall_experience, remember_experience
from mcp.dispatcher import Dispatcher
from mcp_server import _tool_specs
from paths import local_state_dir
from query_engine import QueryEngine


class ExperienceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-experience-")
        self.home = tempfile.mkdtemp(prefix="vc-experience-home-")
        self.old_home = os.environ.get("VC_CONTEXT_HOME")
        os.environ["VC_CONTEXT_HOME"] = self.home
        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        with open(os.path.join(self.root, "src", "auth.py"), "w", encoding="utf-8") as fh:
            fh.write("def auth_guard():\n    pass\n")

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("VC_CONTEXT_HOME", None)
        else:
            os.environ["VC_CONTEXT_HOME"] = self.old_home
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_remember_writes_per_repo_sqlite_store(self) -> None:
        out = remember_experience(
            self.root,
            context_text="auth guard changes",
            content="Reuse the existing auth_guard helper before adding a new one.",
            source_file="src/auth.py",
        )
        self.assertEqual(out["type"], "decision")
        self.assertTrue(os.path.exists(db_path(self.root)))
        self.assertTrue(db_path(self.root).startswith(local_state_dir(self.root)))

    def test_recall_returns_relevant_experience(self) -> None:
        remember_experience(
            self.root,
            context_text="auth guard changes",
            content="Reuse the existing auth_guard helper before adding a new one.",
            source_file="src/auth.py",
        )
        hits = recall_experience(self.root, "changing authentication guard", top_k=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["type"], "decision")
        self.assertFalse(hits[0]["stale"])
        self.assertIn("auth_guard", hits[0]["content"])

    def test_missing_source_file_marks_hit_stale(self) -> None:
        remember_experience(
            self.root,
            context_text="payment webhook edits",
            content="Do not bypass signature verification.",
            source_file="src/missing.py",
        )
        hits = recall_experience(self.root, "webhook signature verification", top_k=1)
        self.assertTrue(hits[0]["stale"])

    def test_query_engine_and_dispatcher_wiring(self) -> None:
        engine = QueryEngine(self.root)
        stored = engine.remember_experience(
            context_text="Angular bridge edits",
            content="Check downgraded bridge files before adding a second wrapper.",
            type="pattern",
            source="user",
        )
        self.assertIn("id", stored)
        hits = Dispatcher(engine).call(
            "recall_experience",
            {"context": "adding angular wrapper bridge", "top_k": 1},
        )
        self.assertEqual(hits[0]["type"], "pattern")

        out = Dispatcher(engine).call(
            "remember_experience",
            {
                "context_text": "SCORM completion",
                "content": "Use semantic_search before guessing service names.",
            },
        )
        self.assertEqual(out["type"], "decision")

    def test_tool_specs_list_experience_tools(self) -> None:
        specs = {s["name"]: s for s in _tool_specs()}
        self.assertEqual(
            specs["remember_experience"]["inputSchema"]["required"],
            ["context_text", "content"],
        )
        self.assertEqual(specs["recall_experience"]["inputSchema"]["required"], ["context"])


if __name__ == "__main__":
    unittest.main()
