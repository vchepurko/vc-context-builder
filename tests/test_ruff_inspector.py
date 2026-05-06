"""Unit tests for ruff_inspector (Feature O).

We mock subprocess.run so the test env doesn't need ruff installed,
and we drive the inspector through both its module API and the
QueryEngine surface (what the MCP server actually calls).
"""

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

import ruff_inspector  # noqa: E402
from query_engine import QueryEngine  # noqa: E402


def _fake_record(code: str, file: str, line: int = 1, message: str = "msg") -> dict:
    """Mimic the shape ruff emits with --output-format=json."""
    return {
        "code": code,
        "filename": file,
        "message": message,
        "location": {"row": line, "column": 1},
        "end_location": {"row": line, "column": 5},
    }


def _patch_run(records: list, *, returncode: int = 1):
    """Patch subprocess.run to return our fake JSON. ruff exits
    non-zero when there are violations (returncode=1)."""
    proc = mock.MagicMock(returncode=returncode)
    proc.stdout = json.dumps(records)
    proc.stderr = ""
    return mock.patch("ruff_inspector.subprocess.run", return_value=proc)


def _force_ruff_enabled(root: str) -> None:
    """Drop a conventions.json that explicitly enables ruff so the
    auto-skip on non-Python projects doesn't short-circuit the
    subprocess mock. ``should_skip_ruff`` honours
    ``ruff.enabled = true`` even when no Python markers are present.
    """
    conv_dir = os.path.join(root, ".vc-context")
    os.makedirs(conv_dir, exist_ok=True)
    with open(os.path.join(conv_dir, "conventions.json"), "w") as fh:
        json.dump({"ruff": {"enabled": True}}, fh)


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-ruff-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _force_ruff_enabled(self.root)

    def test_no_violations_returns_zero_total(self) -> None:
        with _patch_run([]):
            out = ruff_inspector.collect(self.root)
        self.assertEqual(out["total"], 0)
        self.assertEqual(out["by_code"], {})
        self.assertEqual(out["by_file"], {})
        self.assertEqual(out["violations"], [])

    def test_collect_aggregates_by_code_and_file(self) -> None:
        records = [
            _fake_record("UP006", os.path.join(self.root, "a.py")),
            _fake_record("UP006", os.path.join(self.root, "a.py")),
            _fake_record("UP045", os.path.join(self.root, "b.py")),
        ]
        with _patch_run(records):
            out = ruff_inspector.collect(self.root)
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["by_code"], {"UP006": 2, "UP045": 1})
        self.assertEqual(out["by_file"], {"a.py": 2, "b.py": 1})
        self.assertEqual(len(out["violations"]), 3)

    def test_paths_are_normalised_to_project_relative(self) -> None:
        records = [_fake_record("UP006", os.path.join(self.root, "pkg", "x.py"))]
        with _patch_run(records):
            out = ruff_inspector.collect(self.root)
        self.assertEqual(out["violations"][0]["file"], "pkg/x.py")

    def test_filter_by_code_keeps_only_matching(self) -> None:
        records = [
            _fake_record("UP006", os.path.join(self.root, "a.py")),
            _fake_record("UP045", os.path.join(self.root, "a.py")),
        ]
        with _patch_run(records):
            out = ruff_inspector.collect(self.root, code="UP006")
        self.assertEqual(out["total"], 1)
        self.assertEqual([v["code"] for v in out["violations"]], ["UP006"])

    def test_filter_by_path_prefix_is_startswith(self) -> None:
        records = [
            _fake_record("UP006", os.path.join(self.root, "services", "notify", "x.py")),
            _fake_record("UP006", os.path.join(self.root, "tests", "y.py")),
        ]
        with _patch_run(records):
            out = ruff_inspector.collect(self.root, path_prefix="services/notify")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["violations"][0]["file"], "services/notify/x.py")

    def test_summary_drops_violations_list(self) -> None:
        """First-call triage mode: we want by_code/by_file counts
        without dumping every individual violation into the model
        context."""
        records = [_fake_record("UP006", os.path.join(self.root, "a.py"))]
        with _patch_run(records):
            out = ruff_inspector.collect(self.root, summary=True)
        self.assertEqual(out["total"], 1)
        self.assertNotIn("violations", out)

    def test_limit_caps_violations_list(self) -> None:
        records = [
            _fake_record("UP006", os.path.join(self.root, f"f{i}.py"))
            for i in range(10)
        ]
        with _patch_run(records):
            out = ruff_inspector.collect(self.root, limit=3)
        self.assertEqual(out["total"], 10)
        self.assertEqual(len(out["violations"]), 3)
        # by_code / by_file counts still reflect the FULL set.
        self.assertEqual(out["by_code"]["UP006"], 10)

    def test_by_code_sorted_by_count_desc(self) -> None:
        records = (
            [_fake_record("UP045", os.path.join(self.root, "a.py"))] * 1
            + [_fake_record("UP006", os.path.join(self.root, "a.py"))] * 5
        )
        with _patch_run(records):
            out = ruff_inspector.collect(self.root)
        self.assertEqual(list(out["by_code"].keys()), ["UP006", "UP045"])

    def test_ruff_not_installed_returns_empty(self) -> None:
        """Spawn failure (no ruff in PATH) must degrade to empty
        results, not crash the MCP request."""
        with mock.patch(
            "ruff_inspector.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            out = ruff_inspector.collect(self.root)
        self.assertEqual(out["total"], 0)

    def test_garbled_stdout_returns_empty(self) -> None:
        proc = mock.MagicMock(returncode=2)
        proc.stdout = "not json"
        proc.stderr = "internal error"
        with mock.patch("ruff_inspector.subprocess.run", return_value=proc):
            out = ruff_inspector.collect(self.root)
        self.assertEqual(out["total"], 0)

    def test_conventions_override_command(self) -> None:
        """Project-specific projects can swap the ruff invocation
        (e.g. ``poetry run ruff`` instead of ``uv run ruff``) via
        conventions.json."""
        # Overwrite the setUp-installed conventions file so the
        # `command` override is what gets read.
        conv = os.path.join(self.root, ".vc-context")
        os.makedirs(conv, exist_ok=True)
        with open(os.path.join(conv, "conventions.json"), "w") as fh:
            json.dump({"ruff": {
                "enabled": True,
                "command": ["poetry", "run", "ruff", "check",
                            "--output-format=json", "."],
            }}, fh)
        cmd = ruff_inspector._load_command(self.root)
        self.assertEqual(cmd[0], "poetry")


class QueryEngineRuffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-ruff-")
        self.addCleanup(shutil.rmtree, self.root, True)
        with open(os.path.join(self.root, "agent_root.json"), "w") as fh:
            json.dump({"project_root": self.root, "modules": ["."], "roles": {}}, fh)
        _force_ruff_enabled(self.root)

    def test_engine_threads_through_to_collect(self) -> None:
        records = [
            _fake_record("UP006", os.path.join(self.root, "a.py")),
            _fake_record("UP045", os.path.join(self.root, "b.py")),
        ]
        engine = QueryEngine(self.root)
        with _patch_run(records):
            out = engine.ruff_violations()
        self.assertEqual(out["total"], 2)
        self.assertIn("UP006", out["by_code"])

    def test_engine_summary_mode(self) -> None:
        records = [_fake_record("UP006", os.path.join(self.root, "a.py"))]
        engine = QueryEngine(self.root)
        with _patch_run(records):
            out = engine.ruff_violations(summary=True)
        self.assertEqual(out["total"], 1)
        self.assertNotIn("violations", out)


if __name__ == "__main__":
    unittest.main()
