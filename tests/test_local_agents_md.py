"""Unit tests for ``find_local_agents_md`` — walks up the directory
tree and returns every ``AGENTS.md`` found, most-specific first."""

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

from query_engine import QueryEngine


def _write(path: str, body: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


class FindLocalAgentsMdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-agents-md-")
        # Three-level AGENTS.md hierarchy.
        _write(os.path.join(self.root, "AGENTS.md"), "# root rules\n")
        _write(os.path.join(self.root, "bot", "AGENTS.md"), "# bot rules\n")
        _write(
            os.path.join(self.root, "bot", "handlers", "AGENTS.md"),
            "# handlers rules\n",
        )
        # Target file to walk from.
        _write(
            os.path.join(self.root, "bot", "handlers", "admin.py"),
            "# placeholder\n",
        )
        self.engine = QueryEngine(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_walks_up_returning_closest_first(self) -> None:
        out = self.engine.find_local_agents_md("bot/handlers/admin.py")
        files = [r["file"] for r in out]
        self.assertEqual(
            files,
            ["bot/handlers/AGENTS.md", "bot/AGENTS.md", "AGENTS.md"],
        )

    def test_record_carries_size(self) -> None:
        out = self.engine.find_local_agents_md("bot/handlers/admin.py")
        self.assertTrue(all(r["size_bytes"] > 0 for r in out))

    def test_directory_path_is_accepted(self) -> None:
        out = self.engine.find_local_agents_md("bot/handlers")
        files = [r["file"] for r in out]
        self.assertEqual(files[0], "bot/handlers/AGENTS.md")

    def test_path_outside_project_returns_empty(self) -> None:
        self.assertEqual(self.engine.find_local_agents_md("../etc/passwd"), [])

    def test_no_agents_md_anywhere_returns_empty(self) -> None:
        bare = tempfile.mkdtemp(prefix="vc-bare-")
        try:
            os.makedirs(os.path.join(bare, "sub"), exist_ok=True)
            engine = QueryEngine(bare)
            self.assertEqual(engine.find_local_agents_md("sub"), [])
        finally:
            shutil.rmtree(bare, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
