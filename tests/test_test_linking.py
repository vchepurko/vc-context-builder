"""Unit tests for the test-linking heuristic (Feature B)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
if _SUBMODULE not in sys.path:
    sys.path.insert(0, _SUBMODULE)

import test_analysis.test_linking as test_linking
from query_engine import QueryEngine


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class FindTestForSymbolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-tests-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_basic_match(self) -> None:
        _write(os.path.join(self.root, "pkg", "foo.py"), "def my_func(): pass\n")
        _write(
            os.path.join(self.root, "tests", "test_foo.py"),
            "def test_my_func():\n    assert True\n",
        )
        result = test_linking.find_test_for_symbol(
            self.root,
            "my_func",
            "pkg/foo.py",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["test_file"], "tests/test_foo.py")
        self.assertEqual(result["test_function"], "test_my_func")
        self.assertEqual(result["line"], 1)

    def test_no_test_file_returns_none(self) -> None:
        _write(os.path.join(self.root, "pkg", "foo.py"), "def x(): pass\n")
        self.assertIsNone(
            test_linking.find_test_for_symbol(
                self.root,
                "x",
                "pkg/foo.py",
            )
        )

    def test_test_file_exists_but_no_match(self) -> None:
        _write(os.path.join(self.root, "tests", "test_foo.py"), "def test_other_thing(): pass\n")
        self.assertIsNone(
            test_linking.find_test_for_symbol(
                self.root,
                "my_func",
                "pkg/foo.py",
            )
        )

    def test_shortest_match_wins(self) -> None:
        _write(
            os.path.join(self.root, "tests", "test_orders.py"),
            "def test_mark_paid_with_idempotency():\n    pass\ndef test_mark_paid():\n    pass\n",
        )
        result = test_linking.find_test_for_symbol(
            self.root,
            "mark_paid",
            "domain/orders.py",
        )
        self.assertIsNotNone(result)
        # Shorter test name = more specific to the symbol.
        self.assertEqual(result["test_function"], "test_mark_paid")

    def test_case_insensitive_match(self) -> None:
        _write(
            os.path.join(self.root, "tests", "test_payments.py"),
            "def test_LiqPay_Webhook(): pass\n",
        )
        result = test_linking.find_test_for_symbol(
            self.root,
            "liqpay_webhook",
            "x/payments.py",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["test_function"], "test_LiqPay_Webhook")

    def test_async_test_function_supported(self) -> None:
        _write(
            os.path.join(self.root, "tests", "test_foo.py"),
            "async def test_my_async():\n    pass\n",
        )
        result = test_linking.find_test_for_symbol(
            self.root,
            "my_async",
            "x/foo.py",
        )
        self.assertIsNotNone(result)

    def test_widened_glob_matches_basename_prefix(self) -> None:
        """``test_<basename>*.py`` (prefix) must match — not just the
        exact ``test_<basename>.py``. Real-world example:
        ``test_admin_staff_handler.py`` for ``admin_staff.py``."""
        _write(
            os.path.join(self.root, "tests", "test_admin_staff_handler.py"),
            "def test_my_func():\n    pass\n",
        )
        result = test_linking.find_test_for_symbol(
            self.root,
            "my_func",
            "bot/handlers/admin_staff.py",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["test_file"], "tests/test_admin_staff_handler.py")

    def test_widened_glob_does_not_match_unrelated_prefix(self) -> None:
        """``test_foo_bar.py`` is NOT a candidate for ``foo.py`` — the
        basename here is ``foo``, but the test file is for ``foo_bar``.
        The separator-tightening rule blocks this false positive."""
        _write(os.path.join(self.root, "tests", "test_foobar.py"), "def test_my_func(): pass\n")
        result = test_linking.find_test_for_symbol(
            self.root,
            "my_func",
            "x/foo.py",
        )
        self.assertIsNone(result)


class ReferenceBasedLinkingTests(unittest.TestCase):
    """Phase 1 improvement: link by what each ``def test_*`` body
    actually references (imports + patch()), not just by filename
    convention. Covers handler tests where the file is named after
    the feature (``test_admin_staff_handler.py``) but the symbols
    inside live elsewhere."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-tests-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_links_via_direct_import_call(self) -> None:
        _write(
            os.path.join(self.root, "tests", "test_handlers.py"),
            (
                "from bot.handlers.admin_staff import adm_staff_edit_email\n"
                "\n"
                "def test_edit_email_callback():\n"
                "    adm_staff_edit_email()\n"
            ),
        )
        idx = test_linking.build_reference_index(self.root)
        self.assertIn("adm_staff_edit_email", idx)
        result = test_linking.find_test_for_symbol(
            self.root,
            "adm_staff_edit_email",
            "bot/handlers/admin_staff.py",
            reference_index=idx,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["test_function"], "test_edit_email_callback")

    def test_links_via_patch_dotted_string(self) -> None:
        """aiogram-style tests use ``patch("module.path.symbol", mock)``
        — the linker must pull the last segment off and treat it as a
        reference, even when the symbol isn't imported in the test."""
        _write(
            os.path.join(self.root, "tests", "test_x.py"),
            (
                "from unittest.mock import patch, AsyncMock\n"
                "\n"
                "def test_something():\n"
                "    with patch('services.notify.notify', AsyncMock()):\n"
                "        pass\n"
            ),
        )
        idx = test_linking.build_reference_index(self.root)
        self.assertIn("notify", idx)
        result = test_linking.find_test_for_symbol(
            self.root,
            "notify",
            "services/notify/service.py",
            reference_index=idx,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["test_function"], "test_something")

    def test_co_location_bonus_picks_handler_test_when_two_files_reference(self) -> None:
        """Two test files reference the same symbol; the one
        co-located with the source file (test_<basename>*.py) wins
        over a generic test (test_smoke.py) regardless of name length."""
        _write(
            os.path.join(self.root, "tests", "test_admin_staff_handler.py"),
            (
                "from bot.handlers.admin_staff import adm_staff_edit_email\n"
                "\n"
                "def test_long_specific_handler_test():\n"
                "    adm_staff_edit_email()\n"
            ),
        )
        _write(
            os.path.join(self.root, "tests", "test_smoke.py"),
            (
                "from bot.handlers.admin_staff import adm_staff_edit_email\n"
                "\n"
                "def test_smoke():\n"
                "    adm_staff_edit_email()\n"
            ),
        )
        idx = test_linking.build_reference_index(self.root)
        result = test_linking.find_test_for_symbol(
            self.root,
            "adm_staff_edit_email",
            "bot/handlers/admin_staff.py",
            reference_index=idx,
        )
        self.assertIsNotNone(result)
        # Co-location wins despite the longer name.
        self.assertEqual(
            result["test_file"],
            "tests/test_admin_staff_handler.py",
        )

    def test_attribute_access_counts_as_reference(self) -> None:
        """``module.symbol.something`` references ``module`` (binding
        of `import module`) — the linker must catch that, since many
        tests use ``mod.func()`` style after a top-level
        ``import services.admin_service as mod``."""
        _write(
            os.path.join(self.root, "tests", "test_y.py"),
            (
                "import services.admin_service\n"
                "\n"
                "def test_role_lookup():\n"
                "    services.admin_service.get_role()\n"
            ),
        )
        idx = test_linking.build_reference_index(self.root)
        # `import services.admin_service` binds top name "services".
        self.assertIn("services", idx)

    def test_class_method_test_supported(self) -> None:
        """pytest classes — ``class TestX: def test_y`` — must be
        picked up too."""
        _write(
            os.path.join(self.root, "tests", "test_z.py"),
            (
                "from pkg.foo import bar\n"
                "\n"
                "class TestBar:\n"
                "    def test_calls_bar(self):\n"
                "        bar()\n"
            ),
        )
        idx = test_linking.build_reference_index(self.root)
        self.assertIn("bar", idx)
        hits = idx["bar"]
        self.assertEqual(hits[0]["test_function"], "test_calls_bar")

    def test_unrelated_file_not_indexed_as_reference(self) -> None:
        """Files outside ``tests/`` don't get scanned — keeps the
        index focused on actual tests."""
        _write(
            os.path.join(self.root, "src", "consumer.py"),
            ("from pkg.foo import bar\n\ndef test_lookalike():\n    bar()\n"),
        )
        idx = test_linking.build_reference_index(self.root)
        self.assertNotIn("bar", idx)

    def test_build_test_index_uses_reference_path(self) -> None:
        """End-to-end: build_test_index must find the symbol via the
        reference index even when the test file's name doesn't follow
        the co-location convention at all."""
        _write(
            os.path.join(self.root, "tests", "test_completely_unrelated_name.py"),
            ("from pkg.foo import bar\n\ndef test_bar_behavior():\n    bar()\n"),
        )
        symbols = {"bar": {"file": "pkg/foo.py"}}
        index = test_linking.build_test_index(self.root, symbols)
        self.assertIsNotNone(index["bar"])
        self.assertEqual(
            index["bar"]["test_file"],
            "tests/test_completely_unrelated_name.py",
        )


class BuildTestIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-tests-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_build_index_with_mixed_results(self) -> None:
        _write(os.path.join(self.root, "tests", "test_a.py"), "def test_alpha(): pass\n")
        symbols = {
            "alpha": {"file": "x/a.py"},
            "beta": {"file": "x/b.py"},
        }
        index = test_linking.build_test_index(self.root, symbols)
        self.assertIsNotNone(index["alpha"])
        self.assertIsNone(index["beta"])

    def test_write_test_index_persists_to_disk(self) -> None:
        symbols = {"alpha": {"file": "x/a.py"}}
        _write(os.path.join(self.root, "tests", "test_a.py"), "def test_alpha(): pass\n")
        index = test_linking.build_test_index(self.root, symbols)
        out = test_linking.write_test_index(self.root, index)
        self.assertTrue(os.path.exists(out))
        with open(out) as fh:
            payload = json.load(fh)
        self.assertEqual(payload["alpha"]["test_function"], "test_alpha")


class QueryEngineTestLinkingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-tests-")
        self.addCleanup(shutil.rmtree, self.root, True)

        _write_json(
            os.path.join(self.root, "agent_root.json"),
            {
                "project_root": self.root,
                "modules": ["."],
                "roles": {"webhook": ["liqpay_callback"], "route": ["index_route"]},
            },
        )
        _write_json(
            os.path.join(self.root, "agent_symbols.json"),
            {
                "liqpay_callback": {"file": "pkg_a/webhooks.py", "role": "webhook"},
                "index_route": {"file": "pkg_b/routes.py", "role": "route"},
                "loose_helper": {"file": "pkg_b/utils.py"},
            },
        )
        _write_json(
            os.path.join(self.root, "agent_tests.json"),
            {
                "liqpay_callback": {
                    "test_file": "tests/test_webhooks.py",
                    "test_function": "test_liqpay_callback",
                    "line": 5,
                },
                "index_route": None,
                "loose_helper": None,
            },
        )

    def test_find_symbol_includes_test_field(self) -> None:
        engine = QueryEngine(self.root)
        entry = engine.find_symbol("liqpay_callback")
        self.assertIn("test", entry)
        self.assertEqual(entry["test"]["test_function"], "test_liqpay_callback")

    def test_find_symbol_no_test_omits_field(self) -> None:
        engine = QueryEngine(self.root)
        entry = engine.find_symbol("index_route")
        self.assertNotIn("test", entry)

    def test_find_test_returns_record(self) -> None:
        engine = QueryEngine(self.root)
        result = engine.find_test("liqpay_callback")
        self.assertEqual(result["test_function"], "test_liqpay_callback")

    def test_find_test_returns_none_for_unlinked(self) -> None:
        engine = QueryEngine(self.root)
        self.assertIsNone(engine.find_test("index_route"))

    def test_coverage_stats_shape(self) -> None:
        engine = QueryEngine(self.root)
        stats = engine.coverage_stats()
        self.assertIn("overall", stats)
        self.assertEqual(stats["overall"]["total"], 3)
        self.assertEqual(stats["overall"]["with_test"], 1)
        self.assertEqual(stats["webhook"]["with_test"], 1)
        self.assertEqual(stats["route"]["with_test"], 0)


class CliTestSubcommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-tests-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _write_json(
            os.path.join(self.root, "agent_root.json"),
            {
                "project_root": self.root,
                "modules": ["."],
                "roles": {},
            },
        )
        _write_json(
            os.path.join(self.root, "agent_symbols.json"),
            {
                "alpha": {"file": "x/a.py"},
            },
        )
        _write_json(
            os.path.join(self.root, "agent_tests.json"),
            {
                "alpha": {
                    "test_file": "tests/test_a.py",
                    "test_function": "test_alpha",
                    "line": 3,
                },
            },
        )

    def test_cli_test_hit(self) -> None:
        cli = os.path.join(_SUBMODULE, "cli.py")
        r = subprocess.run(
            [sys.executable, cli, "--root", self.root, "--json", "test", "alpha"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["test_function"], "test_alpha")

    def test_cli_test_miss(self) -> None:
        cli = os.path.join(_SUBMODULE, "cli.py")
        r = subprocess.run(
            [sys.executable, cli, "--root", self.root, "test", "ghost"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 1)

    def test_cli_coverage_runs(self) -> None:
        cli = os.path.join(_SUBMODULE, "cli.py")
        r = subprocess.run(
            [sys.executable, cli, "--root", self.root, "--json", "coverage"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["overall"]["with_test"], 1)


if __name__ == "__main__":
    unittest.main()
