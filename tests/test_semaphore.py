"""Tests for named file-backed semaphores (deploy-slot style mutual exclusion)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

from semaphore import acquire, force_break, list_locks, release, render_active_locks, status

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
_CLI = os.path.join(_SUBMODULE, "cli.py")


def _run(root: str, *cli_args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, _CLI, "--root", root, *cli_args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


class SemaphoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="vc-semaphore-")
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_first_acquire_succeeds_and_records_the_holder(self) -> None:
        out = acquire(self.root, "deploy-engine", agent="agent-a", task="rebuild image")

        self.assertTrue(out["acquired"])
        self.assertEqual(out["held_by"]["agent"], "agent-a")
        self.assertEqual(out["held_by"]["task"], "rebuild image")
        self.assertTrue(os.path.exists(out["lock"]))

    def test_second_acquire_of_the_same_name_fails_and_reports_the_real_holder(self) -> None:
        acquire(self.root, "deploy-engine", agent="agent-a", task="first")
        out = acquire(self.root, "deploy-engine", agent="agent-b", task="second")

        self.assertFalse(out["acquired"])
        self.assertEqual(out["held_by"]["agent"], "agent-a")
        self.assertEqual(out["held_by"]["task"], "first")

    def test_acquire_of_a_different_name_does_not_contend_with_an_existing_lock(self) -> None:
        acquire(self.root, "deploy-engine", agent="agent-a")
        out = acquire(self.root, "deploy-platform", agent="agent-b")

        self.assertTrue(out["acquired"])

    def test_acquire_without_an_agent_identity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            acquire(self.root, "deploy-engine", agent="")

    def test_release_by_the_holder_frees_the_lock_for_the_next_claimant(self) -> None:
        acquire(self.root, "deploy-engine", agent="agent-a")
        out = release(self.root, "deploy-engine", agent="agent-a")
        self.assertTrue(out["released"])

        reacquired = acquire(self.root, "deploy-engine", agent="agent-b")
        self.assertTrue(reacquired["acquired"])

    def test_release_by_a_non_holder_is_refused_not_silently_dropped(self) -> None:
        """Both polarity of release: the OWNER can release, a NON-owner cannot —
        a blind unlink would let agent B drop agent A's still-active claim."""
        acquire(self.root, "deploy-engine", agent="agent-a")
        out = release(self.root, "deploy-engine", agent="agent-b")

        self.assertFalse(out["released"])
        self.assertEqual(out["reason"], "held_by_other")
        still_held = status(self.root, "deploy-engine")
        self.assertTrue(still_held["held"])
        self.assertEqual(still_held["held_by"]["agent"], "agent-a")

    def test_release_of_an_unlocked_name_is_a_reported_no_op(self) -> None:
        out = release(self.root, "never-locked", agent="agent-a")
        self.assertFalse(out["released"])
        self.assertEqual(out["reason"], "not_locked")

    def test_force_break_clears_the_lock_regardless_of_holder_and_requires_a_reason(self) -> None:
        acquire(self.root, "deploy-engine", agent="agent-a", task="stuck build")
        with self.assertRaises(ValueError):
            force_break(self.root, "deploy-engine", agent="rescuer", reason="")

        out = force_break(self.root, "deploy-engine", agent="rescuer", reason="crashed session")
        self.assertTrue(out["broken"])
        self.assertEqual(out["was_held_by"]["agent"], "agent-a")
        self.assertFalse(status(self.root, "deploy-engine")["held"])

    def test_force_break_archives_an_auditable_record_of_who_broke_it_and_why(self) -> None:
        acquire(self.root, "deploy-engine", agent="agent-a")
        force_break(self.root, "deploy-engine", agent="rescuer", reason="crashed session")

        history_dir = os.path.join(self.root, ".vc-context", "locks", "history")
        entries = os.listdir(history_dir)
        self.assertEqual(len(entries), 1)
        with open(os.path.join(history_dir, entries[0]), encoding="utf-8") as fh:
            record = json.load(fh)
        self.assertEqual(record["event"], "force_break")
        self.assertEqual(record["by"], "rescuer")
        self.assertEqual(record["reason"], "crashed session")
        self.assertEqual(record["held_by"]["agent"], "agent-a")

    def test_status_of_a_free_name_reports_held_false(self) -> None:
        out = status(self.root, "never-locked")
        self.assertFalse(out["held"])

    def test_status_reports_age_and_flags_a_long_held_lock_as_possibly_stale(self) -> None:
        acquire(self.root, "deploy-engine", agent="agent-a")
        fresh = status(self.root, "deploy-engine")
        self.assertFalse(fresh["possibly_stale"])

        lock_path = os.path.join(self.root, ".vc-context", "locks", "deploy-engine.lock")
        with open(lock_path, encoding="utf-8") as fh:
            record = json.load(fh)
        record["acquired_at_epoch"] -= 7200  # simulate a lock held 2h ago
        with open(lock_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)

        stale = status(self.root, "deploy-engine")
        self.assertTrue(stale["possibly_stale"])
        self.assertTrue(stale["held"])  # still held -- staleness is a HINT, never auto-broken

    def test_list_locks_returns_every_held_lock_and_render_reflects_them_in_markdown(self) -> None:
        self.assertEqual(list_locks(self.root), [])
        self.assertIn("None held", render_active_locks(self.root))

        acquire(self.root, "deploy-engine", agent="agent-a", task="rebuild")
        acquire(self.root, "deploy-platform", agent="agent-b", task="migrate")

        names = {lock["name"] for lock in list_locks(self.root)}
        self.assertEqual(names, {"deploy-engine", "deploy-platform"})
        rendered = render_active_locks(self.root)
        self.assertIn("deploy-engine", rendered)
        self.assertIn("agent-a", rendered)
        self.assertIn("deploy-platform", rendered)
        self.assertIn("agent-b", rendered)

    def test_cli_acquire_then_status_json_round_trips_through_subprocess(self) -> None:
        result = _run(self.root, "lock", "acquire", "--name", "deploy-engine", "--agent", "cli-agent", "--task", "t")
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        result = _run(self.root, "--json", "lock", "status", "--name", "deploy-engine")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["held"])
        self.assertEqual(payload["held_by"]["agent"], "cli-agent")

    def test_cli_second_acquire_exits_nonzero_so_scripts_can_branch_on_it(self) -> None:
        _run(self.root, "lock", "acquire", "--name", "deploy-engine", "--agent", "agent-a")
        result = _run(self.root, "lock", "acquire", "--name", "deploy-engine", "--agent", "agent-b")
        self.assertNotEqual(result.returncode, 0)

    def test_cli_break_requires_a_reason_argument(self) -> None:
        _run(self.root, "lock", "acquire", "--name", "deploy-engine", "--agent", "agent-a")
        result = _run(self.root, "lock", "break", "--name", "deploy-engine", "--agent", "rescuer")
        self.assertNotEqual(result.returncode, 0)  # argparse rejects: --reason is required


if __name__ == "__main__":
    unittest.main()
