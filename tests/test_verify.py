"""Tests for the typed `verify(kind, subject, target?)` primitive.

Each kind is exercised with a positive AND negative case so the
evidence-string contract stays stable. The fixture mirrors the
shape of `agent_symbols.json` directly — no need to spin up a real
indexer pass for what is fundamentally a projection over the index.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from query_engine import QueryEngine


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-verify-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _write(
            os.path.join(self.root, "agent_root.json"),
            json.dumps({"project_root": self.root, "modules": ["./pkg"], "roles": {}}),
        )
        _write(
            os.path.join(self.root, "agent_symbols.json"),
            json.dumps(
                {
                    "do_work": {
                        "file": "pkg/work.py",
                        "line": 10,
                        "kind": "func",
                        "callees": ["fetch", "log_event"],
                        "raises": ["ValueError"],
                        "decorators": ["app.post", "cached"],
                    },
                    "MyService": {
                        "file": "pkg/svc.py",
                        "line": 1,
                        "kind": "class",
                    },
                }
            ),
        )
        self.engine = QueryEngine(self.root)


class ExistsKindTests(_Fixture):
    def test_known_symbol(self) -> None:
        out = self.engine.verify("exists", "do_work")
        self.assertTrue(out["result"])
        self.assertIn("pkg/work.py", out["evidence"])
        self.assertEqual(out["kind"], "exists")

    def test_unknown_symbol(self) -> None:
        out = self.engine.verify("exists", "ghost")
        self.assertFalse(out["result"])
        self.assertIn("not in agent_symbols.json", out["evidence"])


class CallsKindTests(_Fixture):
    def test_calls_target_present(self) -> None:
        out = self.engine.verify("calls", "do_work", "fetch")
        self.assertTrue(out["result"])
        self.assertIn("∋ fetch", out["evidence"])

    def test_calls_target_absent(self) -> None:
        out = self.engine.verify("calls", "do_work", "send_email")
        self.assertFalse(out["result"])
        self.assertIn("send_email absent", out["evidence"])

    def test_calls_requires_target(self) -> None:
        out = self.engine.verify("calls", "do_work", target=None)
        self.assertFalse(out["result"])
        self.assertIn("requires non-empty target", out["evidence"])


class RaisesKindTests(_Fixture):
    def test_raises_target_present(self) -> None:
        out = self.engine.verify("raises", "do_work", "ValueError")
        self.assertTrue(out["result"])

    def test_raises_target_absent(self) -> None:
        out = self.engine.verify("raises", "do_work", "TypeError")
        self.assertFalse(out["result"])
        self.assertIn("TypeError absent", out["evidence"])


class DecoratedKindTests(_Fixture):
    def test_exact_match(self) -> None:
        out = self.engine.verify("decorated", "do_work", "cached")
        self.assertTrue(out["result"])

    def test_suffix_match(self) -> None:
        # "post" should match "app.post"
        out = self.engine.verify("decorated", "do_work", "post")
        self.assertTrue(out["result"])

    def test_absent(self) -> None:
        out = self.engine.verify("decorated", "do_work", "deprecated")
        self.assertFalse(out["result"])

    def test_no_decorators_field(self) -> None:
        out = self.engine.verify("decorated", "MyService", "anything")
        self.assertFalse(out["result"])


class UnknownKindTests(_Fixture):
    def test_unknown_kind_returns_false(self) -> None:
        out = self.engine.verify("nonsense", "do_work")
        self.assertFalse(out["result"])
        self.assertIn("unknown verify kind", out["evidence"])

    def test_empty_subject(self) -> None:
        out = self.engine.verify("exists", "")
        self.assertFalse(out["result"])
        self.assertIn("empty subject", out["evidence"])


if __name__ == "__main__":
    unittest.main()
