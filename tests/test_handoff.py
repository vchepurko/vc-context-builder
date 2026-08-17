"""Tests for project-local HANDOFF memory."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

from handoff import (
    HANDOFF_LOG_DIR,
    HANDOFF_PATH,
    init_handoff,
    prompt_handoff,
    snapshot_handoff,
    status_handoff,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
_CLI = os.path.join(_SUBMODULE, "cli.py")


def _run(root: str, *cli_args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, _CLI, "--root", root, *cli_args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


class HandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="vc-handoff-")
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_init_creates_pointer_and_memory(self) -> None:
        out = init_handoff(self.root, task="catalog", agent="codex")

        self.assertTrue(out["pointer_written"])
        self.assertTrue(out["memory_written"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "HANDOFF.md")))
        memory = os.path.join(self.root, HANDOFF_PATH)
        self.assertTrue(os.path.exists(memory))
        with open(memory, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Task: catalog", text)
        self.assertIn("Agent: codex", text)

    def test_init_is_idempotent_without_force(self) -> None:
        init_handoff(self.root, task="first", agent="codex")
        out = init_handoff(self.root, task="second", agent="gemini")

        self.assertFalse(out["pointer_written"])
        self.assertFalse(out["memory_written"])
        status = status_handoff(self.root)
        self.assertEqual(status["task"], "first")
        self.assertEqual(status["agent"], "codex")

    def test_snapshot_rewrites_memory_and_status_reads_next_step(self) -> None:
        out = snapshot_handoff(
            self.root,
            task="handoff-feature",
            agent="gemini-2",
            status="blocked",
            next_step="Run tests after dependency install.",
            notes=["Added CLI command."],
            blockers=["Dependency cache unavailable."],
        )

        self.assertTrue(out["pointer_written"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "HANDOFF.md")))
        self.assertTrue(os.path.exists(out["log"]))
        self.assertIn(HANDOFF_LOG_DIR, out["log"])
        status = status_handoff(self.root)
        self.assertTrue(status["pointer_exists"])
        self.assertTrue(status["memory_exists"])
        self.assertEqual(status["task"], "handoff-feature")
        self.assertEqual(status["status"], "blocked")
        self.assertEqual(status["agent"], "gemini-2")
        self.assertIn("Run tests", status["next_step"])
        self.assertEqual(status["latest_log"], out["log"])

    def test_prompt_names_handoff_files(self) -> None:
        text = prompt_handoff(self.root, agent="gemini-2")

        self.assertIn("gemini-2", text)
        self.assertIn("HANDOFF.md", text)
        self.assertIn(HANDOFF_PATH, text)
        self.assertIn(HANDOFF_LOG_DIR, text)

    def test_cli_handoff_json_status(self) -> None:
        result = _run(
            self.root,
            "handoff",
            "snapshot",
            "--task",
            "cli-task",
            "--agent",
            "codex",
            "--next-step",
            "Continue in another chat.",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        result = _run(self.root, "--json", "handoff", "status")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["task"], "cli-task")
        self.assertEqual(payload["agent"], "codex")
        self.assertIn("Continue", payload["next_step"])


if __name__ == "__main__":
    unittest.main()
