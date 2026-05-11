"""Tests for ``configs_scanner.scan`` — the ``find_pattern_in_configs``
backend that replaces ``grep -rn`` for non-code config files.

Builds a tiny synthetic project tree under tmp/ so each test sees a
known set of config files and unrelated code/binary noise that the
scanner should skip.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from configs_scanner import ALL_KINDS, list_kinds, scan


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class _FixtureMixin:
    """Synthetic project tree:

    .env                       — env file with the pattern
    .env.production            — env variant with the pattern
    deploy/Caddyfile           — Caddy config
    docker-compose.yml         — yaml (compose)
    .github/workflows/ci.yml   — github-actions yaml
    pyproject.toml             — toml
    config/app.conf            — generic conf
    alembic.ini                — ini
    bot.py                     — code (must be SKIPPED)
    .git/HEAD                  — git internals (must be SKIPPED)
    node_modules/lib.yml       — ignored dir (must be SKIPPED)
    """

    def _make_root(self) -> str:
        root = tempfile.mkdtemp(prefix="configs_scanner_")
        _write(os.path.join(root, ".env"), "BOT_TOKEN=abc\nFERNET_KEY=k1\n")
        _write(
            os.path.join(root, ".env.production"),
            "FERNET_KEY=PRODK1\nBACKUP_FERNET_KEY=PRODK2\n",
        )
        _write(
            os.path.join(root, "deploy/Caddyfile"),
            (
                "shop.example {\n"
                "  reverse_proxy bot:8080\n"
                "  header { X-Frame-Options SAMEORIGIN }\n"
                "}\n"
            ),
        )
        _write(
            os.path.join(root, "docker-compose.yml"),
            "services:\n  bot:\n    image: x\n    restart: unless-stopped\n",
        )
        _write(
            os.path.join(root, ".github/workflows/ci.yml"),
            "jobs:\n  test:\n    env:\n      FERNET_KEY: xxx\n",
        )
        _write(
            os.path.join(root, "pyproject.toml"),
            '[project]\nname = "demo"\ndependencies = ["cryptography>=47"]\n',
        )
        _write(
            os.path.join(root, "config/app.conf"),
            "listen 8080\nFERNET_KEY mention here\n",
        )
        _write(
            os.path.join(root, "alembic.ini"),
            "[alembic]\nscript_location = alembic\n",
        )
        # Code — must be ignored even though it contains the pattern.
        _write(os.path.join(root, "bot.py"), "FERNET_KEY = 'in-code-not-found'\n")
        # Ignored-dir noise — must be skipped entirely.
        _write(
            os.path.join(root, "node_modules/lib.yml"),
            "secret: FERNET_KEY in deps\n",
        )
        _write(os.path.join(root, ".git/HEAD"), "ref: refs/heads/main\n")
        return root


class TestScanBasics(unittest.TestCase, _FixtureMixin):
    def setUp(self) -> None:
        self.root = self._make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_empty_pattern_returns_empty(self) -> None:
        self.assertEqual(scan(self.root, ""), [])

    def test_unknown_kind_returns_empty(self) -> None:
        # Caller typo: 'yam' instead of 'yaml' → empty, not crash.
        self.assertEqual(scan(self.root, "FERNET", kinds=["yam"]), [])

    def test_finds_pattern_across_kinds(self) -> None:
        hits = scan(self.root, "FERNET")
        files = {h["file"] for h in hits}
        # env + env.production + workflows yml + app.conf — all referenced FERNET.
        self.assertIn(".env", files)
        self.assertIn(".env.production", files)
        self.assertIn(".github/workflows/ci.yml", files)
        self.assertIn("config/app.conf", files)

    def test_skips_python_code(self) -> None:
        # bot.py contains FERNET_KEY but it's code, scanner must skip.
        hits = scan(self.root, "FERNET")
        files = {h["file"] for h in hits}
        self.assertNotIn("bot.py", files)

    def test_skips_ignored_dirs(self) -> None:
        # node_modules/lib.yml has FERNET, but the dir is in IGNORE.
        hits = scan(self.root, "FERNET")
        files = {h["file"] for h in hits}
        self.assertFalse(any("node_modules" in f for f in files))

    def test_skips_git_dir(self) -> None:
        # .git/HEAD must never appear (different pattern, but the dir
        # is ignored before file-level filters even run).
        hits = scan(self.root, "ref:")
        files = {h["file"] for h in hits}
        self.assertFalse(any(".git/" in f for f in files))

    def test_kind_filter_narrows_results(self) -> None:
        # Only env files — drops Caddyfile, yml, conf.
        hits = scan(self.root, "FERNET", kinds=["env"])
        kinds_found = {h["kind"] for h in hits}
        self.assertEqual(kinds_found, {"env"})
        files = {h["file"] for h in hits}
        self.assertNotIn(".github/workflows/ci.yml", files)
        self.assertNotIn("config/app.conf", files)

    def test_case_insensitive_default(self) -> None:
        # 'fernet' lowercase finds FERNET_KEY entries.
        hits = scan(self.root, "fernet")
        self.assertGreater(len(hits), 0)

    def test_case_sensitive_strict(self) -> None:
        # 'fernet' lowercase, case-sensitive — no match (all are FERNET).
        hits = scan(self.root, "fernet", case_sensitive=True)
        self.assertEqual(hits, [])

    def test_regex_mode(self) -> None:
        # Match FERNET_KEY at start of line — regex `^FERNET_KEY=`.
        hits = scan(self.root, r"^FERNET_KEY=", use_regex=True)
        # .env has `FERNET_KEY=k1` at line 2; .env.production has it at line 1.
        files_lines = {(h["file"], h["line"]) for h in hits}
        self.assertIn((".env", 2), files_lines)
        self.assertIn((".env.production", 1), files_lines)

    def test_limit_short_circuits(self) -> None:
        # Pattern matches many lines; limit=2 returns at most 2.
        hits = scan(self.root, "FERNET", limit=2)
        self.assertEqual(len(hits), 2)

    def test_returned_shape(self) -> None:
        hits = scan(self.root, "X-Frame-Options")
        self.assertEqual(len(hits), 1)
        h = hits[0]
        self.assertEqual(h["file"], "deploy/Caddyfile")
        self.assertEqual(h["kind"], "caddy")
        self.assertIn("X-Frame-Options", h["text"])  # type: ignore[arg-type]
        self.assertIsInstance(h["line"], int)

    def test_caddyfile_matches_by_name(self) -> None:
        # Caddyfile has no extension — confirms our name-based glob works.
        hits = scan(self.root, "reverse_proxy", kinds=["caddy"])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["file"], "deploy/Caddyfile")

    def test_yaml_kind_finds_compose_and_actions(self) -> None:
        hits = scan(self.root, "restart", kinds=["yaml"])
        files = {h["file"] for h in hits}
        self.assertIn("docker-compose.yml", files)

    def test_github_actions_kind(self) -> None:
        # Workflows live under .github/workflows; specifically targeted.
        hits = scan(self.root, "FERNET", kinds=["github-actions"])
        files = {h["file"] for h in hits}
        self.assertEqual(files, {".github/workflows/ci.yml"})

    def test_toml_kind(self) -> None:
        hits = scan(self.root, "cryptography", kinds=["toml"])
        files = {h["file"] for h in hits}
        self.assertEqual(files, {"pyproject.toml"})


class TestListKinds(unittest.TestCase):
    def test_includes_expected_kinds(self) -> None:
        kinds = list_kinds()
        for k in ("env", "yaml", "toml", "ini", "caddy", "dockerfile", "github-actions"):
            self.assertIn(k, kinds)

    def test_kinds_are_sorted(self) -> None:
        kinds = list_kinds()
        self.assertEqual(kinds, sorted(kinds))

    def test_set_matches_module_constant(self) -> None:
        self.assertEqual(set(list_kinds()), set(ALL_KINDS))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
