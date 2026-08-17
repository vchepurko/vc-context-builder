"""Tests for agent startup shared-session helpers."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from agent_session import DEFAULT_STALE_SECONDS, agent_start, maybe_reindex


def _write_json(path: str, payload: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class AgentSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-agent-start-")
        self.home = tempfile.mkdtemp(prefix="vc-agent-start-home-")
        self._old_home = os.environ.get("VC_CONTEXT_HOME")
        os.environ["VC_CONTEXT_HOME"] = self.home
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.home, True)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("VC_CONTEXT_HOME", None)
        else:
            os.environ["VC_CONTEXT_HOME"] = self._old_home

    def _write_agent_root(self, *, age_seconds: int) -> None:
        path = os.path.join(self.root, ".vc-context", "index", "agent_root.json")
        _write_json(path, {"modules": []})
        mtime = time.time() - age_seconds
        os.utime(path, (mtime, mtime))

    def test_maybe_reindex_skips_fresh_index(self) -> None:
        self._write_agent_root(age_seconds=10)

        with mock.patch("agent_session.subprocess.run") as run:
            out = maybe_reindex(self.root, mode="auto", stale_seconds=DEFAULT_STALE_SECONDS)

        self.assertTrue(out["ok"])
        self.assertFalse(out["ran"])
        run.assert_not_called()

    def test_maybe_reindex_runs_for_missing_index(self) -> None:
        with mock.patch("agent_session.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stderr = ""
            out = maybe_reindex(self.root, mode="auto", stale_seconds=DEFAULT_STALE_SECONDS)

        self.assertTrue(out["ok"])
        self.assertTrue(out["ran"])
        args = run.call_args.args[0]
        self.assertIn("agent_map.py", args[1])
        self.assertEqual(args[-2:], ["--root", self.root])

    def test_agent_start_returns_prompt_and_handoff_status(self) -> None:
        with mock.patch("agent_session.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stderr = ""
            out = agent_start(self.root, agent="codex", reindex="auto")

        self.assertEqual(out["agent"], "codex")
        self.assertTrue(out["reindex"]["ran"])
        self.assertIn("Agent-start checklist", out["prompt"])
        self.assertIn("codex", out["prompt"])
        self.assertIn("handoff", out)


if __name__ == "__main__":
    unittest.main()
