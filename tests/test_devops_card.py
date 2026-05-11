"""Tests for ``devops_card.build`` — the unified deployment snapshot.

Each test plants a tiny synthetic project tree and asserts the
extracted dict shape. Stdlib parsers are deliberately lossy; tests
pin only the fields agents actually need, not strict round-trip.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from devops_card import build


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class TestEmptyProject(unittest.TestCase):
    def test_empty_root_returns_skeleton(self) -> None:
        root = tempfile.mkdtemp(prefix="devops_card_")
        try:
            card = build(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        self.assertEqual(card["compose_files"], [])
        self.assertEqual(card["dockerfiles"], [])
        self.assertEqual(card["caddy_sites"], [])
        self.assertEqual(card["workflows"], [])
        self.assertEqual(card["scheduler_jobs"], [])


class TestComposeParser(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="devops_card_compose_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_basic_service_extraction(self) -> None:
        _write(
            os.path.join(self.root, "docker-compose.yml"),
            (
                "services:\n"
                "  bot:\n"
                "    image: python:3.13-slim\n"
                "    restart: unless-stopped\n"
                "    ports:\n"
                '      - "8080:8080"\n'
                "    env_file:\n"
                "      - .env\n"
                "  postgres:\n"
                "    image: postgres:16\n"
                "    restart: always\n"
            ),
        )
        card = build(self.root)
        self.assertEqual(len(card["compose_files"]), 1)
        services = card["compose_files"][0]["services"]
        names = {s["name"] for s in services}
        self.assertEqual(names, {"bot", "postgres"})
        bot = next(s for s in services if s["name"] == "bot")
        self.assertEqual(bot["image"], "python:3.13-slim")
        self.assertEqual(bot["restart"], "unless-stopped")
        self.assertEqual(bot["ports"], ["8080:8080"])
        self.assertEqual(bot["env_file"], [".env"])
        pg = next(s for s in services if s["name"] == "postgres")
        self.assertEqual(pg["restart"], "always")

    def test_handles_build_directive(self) -> None:
        _write(
            os.path.join(self.root, "docker-compose.yml"),
            "services:\n  app:\n    build: .\n    restart: unless-stopped\n",
        )
        card = build(self.root)
        app = card["compose_files"][0]["services"][0]
        self.assertEqual(app["build"], ".")
        self.assertEqual(app["restart"], "unless-stopped")

    def test_nested_compose_files_discovered(self) -> None:
        # Some projects put DB stack in a sub-folder.
        _write(
            os.path.join(self.root, "docker-compose.yml"),
            "services:\n  bot:\n    image: x\n",
        )
        _write(
            os.path.join(self.root, "postgres-stack/docker-compose.yml"),
            "services:\n  postgres:\n    image: postgres:16\n",
        )
        card = build(self.root)
        paths = {f["path"] for f in card["compose_files"]}
        self.assertEqual(paths, {"docker-compose.yml", "postgres-stack/docker-compose.yml"})


class TestDockerfileParser(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="devops_card_docker_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_extracts_headlines(self) -> None:
        _write(
            os.path.join(self.root, "Dockerfile"),
            (
                "FROM python:3.13-slim\n"
                "WORKDIR /app\n"
                "COPY . .\n"
                "EXPOSE 8080\n"
                'ENTRYPOINT ["python", "main.py"]\n'
                'CMD ["--debug"]\n'
            ),
        )
        card = build(self.root)
        df = card["dockerfiles"][0]
        self.assertEqual(df["path"], "Dockerfile")
        self.assertEqual(df["from"], "python:3.13-slim")
        self.assertEqual(df["expose"], [8080])
        self.assertIn("python", df["entrypoint"])
        self.assertIn("--debug", df["cmd"])

    def test_multi_stage_keeps_last_from(self) -> None:
        _write(
            os.path.join(self.root, "Dockerfile"),
            "FROM golang AS builder\nFROM alpine\nEXPOSE 80\n",
        )
        df = build(self.root)["dockerfiles"][0]
        self.assertEqual(df["from"], "alpine")
        self.assertEqual(df["expose"], [80])

    def test_variant_dockerfile_picked_up(self) -> None:
        _write(os.path.join(self.root, "Dockerfile.prod"), "FROM nginx\n")
        paths = {d["path"] for d in build(self.root)["dockerfiles"]}
        self.assertEqual(paths, {"Dockerfile.prod"})


class TestCaddyfileParser(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="devops_card_caddy_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_simple_site_block(self) -> None:
        _write(
            os.path.join(self.root, "Caddyfile"),
            "shop.example.com {\n  reverse_proxy bot:8080\n}\n",
        )
        sites = build(self.root)["caddy_sites"]
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0]["domain"], "shop.example.com")
        self.assertIn("bot:8080", sites[0]["upstreams"])
        self.assertEqual(sites[0]["file"], "Caddyfile")

    def test_nested_header_block(self) -> None:
        # Nested block (header { ... }) inside a site — the parser must
        # not get confused and still extract reverse_proxy.
        _write(
            os.path.join(self.root, "deploy/Caddyfile"),
            (
                "shop.example {\n"
                "  header {\n"
                "    X-Content-Type-Options nosniff\n"
                "  }\n"
                "  reverse_proxy backend:9000\n"
                "}\n"
            ),
        )
        sites = build(self.root)["caddy_sites"]
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0]["domain"], "shop.example")
        self.assertIn("backend:9000", sites[0]["upstreams"])

    def test_multi_domain_split(self) -> None:
        _write(
            os.path.join(self.root, "Caddyfile"),
            "a.example, b.example {\n  reverse_proxy api:1234\n}\n",
        )
        domains = {s["domain"] for s in build(self.root)["caddy_sites"]}
        self.assertEqual(domains, {"a.example", "b.example"})


class TestWorkflowsList(unittest.TestCase):
    def test_lists_yaml_workflows(self) -> None:
        root = tempfile.mkdtemp(prefix="devops_card_wf_")
        try:
            _write(os.path.join(root, ".github/workflows/ci.yml"), "name: ci\n")
            _write(os.path.join(root, ".github/workflows/deploy.yaml"), "name: deploy\n")
            _write(os.path.join(root, ".github/workflows/README.md"), "# notes\n")
            card = build(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        self.assertEqual(
            sorted(card["workflows"]),
            [".github/workflows/ci.yml", ".github/workflows/deploy.yaml"],
        )


class TestSchedulerJobsPassthrough(unittest.TestCase):
    def test_pre_fetched_jobs_returned_verbatim(self) -> None:
        root = tempfile.mkdtemp(prefix="devops_card_sched_")
        try:
            card = build(root, scheduler_jobs=["update_currency_cache", "send_error_digest"])
        finally:
            shutil.rmtree(root, ignore_errors=True)
        self.assertEqual(card["scheduler_jobs"], ["update_currency_cache", "send_error_digest"])


class TestIntegration(unittest.TestCase):
    def test_full_card_with_all_artefacts(self) -> None:
        root = tempfile.mkdtemp(prefix="devops_card_full_")
        try:
            _write(
                os.path.join(root, "docker-compose.yml"),
                "services:\n  bot:\n    image: x\n    restart: unless-stopped\n",
            )
            _write(os.path.join(root, "Dockerfile"), "FROM python:3.13\nEXPOSE 8080\n")
            _write(
                os.path.join(root, "deploy/Caddyfile"),
                "klodchik.example {\n  reverse_proxy bot:8080\n}\n",
            )
            _write(os.path.join(root, ".github/workflows/ci.yml"), "name: ci\n")
            card = build(root, scheduler_jobs=["close_expired_auctions"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

        self.assertEqual(len(card["compose_files"]), 1)
        self.assertEqual(len(card["dockerfiles"]), 1)
        self.assertEqual(len(card["caddy_sites"]), 1)
        self.assertEqual(card["workflows"], [".github/workflows/ci.yml"])
        self.assertEqual(card["scheduler_jobs"], ["close_expired_auctions"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
