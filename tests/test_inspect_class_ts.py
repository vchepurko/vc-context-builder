"""Cross-language ``inspect_class`` — TS class fall-through.

Pins the behaviour that ``inspect_class("AngularComponent")`` returns
the same shape as for Python classes (``{name, file, line, doc,
bases, fields, methods}``) instead of the Python-only ``None``.
"""

from __future__ import annotations

import json
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


class InspectClassTsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="vc-ts-inspect-")
        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        with open(os.path.join(self.root, "src", "profile.component.ts"), "w") as fh:
            fh.write(
                "import { Component, Input, Output, EventEmitter } from '@angular/core';\n"
                "import { UserService } from './user.service';\n"
                "\n"
                "@Component({selector: 'app-profile', template: ''})\n"
                "export class ProfileComponent extends Base implements OnInit, OnDestroy {\n"
                "  @Input() userId!: number;\n"
                "  @Output() save = new EventEmitter<void>();\n"
                "\n"
                "  constructor(private user: UserService, public store: Store<State>) {}\n"
                "\n"
                "  ngOnInit() {}\n"
                "  ngOnDestroy() {}\n"
                "  public refresh() { return this.user.reload(); }\n"
                "  doExport(): void {}\n"
                "  private _internal() {}\n"
                "}\n"
            )
        # Seed minimal symbol index so inspect_class can find the file.
        idx_dir = os.path.join(self.root, ".vc-context", "index")
        os.makedirs(idx_dir, exist_ok=True)
        with open(os.path.join(idx_dir, "agent_symbols.json"), "w") as fh:
            json.dump(
                {
                    "ProfileComponent": {
                        "file": "src/profile.component.ts",
                        "line": 5,
                        "kind": "class",
                        "doc": "Profile editor.",
                    },
                },
                fh,
            )
        self.engine = QueryEngine(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_ts_class_returns_inspect_shape(self) -> None:
        out = self.engine.inspect_class("ProfileComponent")
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["name"], "ProfileComponent")
        self.assertEqual(out["file"], "src/profile.component.ts")
        self.assertEqual(out["line"], 5)
        self.assertEqual(out["doc"], "Profile editor.")

    def test_ts_class_bases_extracted(self) -> None:
        out = self.engine.inspect_class("ProfileComponent")
        assert out is not None
        # ``extends Base`` + ``implements OnInit, OnDestroy``.
        bases = out["bases"]
        self.assertIn("Base", bases)
        self.assertIn("OnInit", bases)
        self.assertIn("OnDestroy", bases)

    def test_ts_class_fields_include_inputs_outputs_and_ctor_params(self) -> None:
        out = self.engine.inspect_class("ProfileComponent")
        assert out is not None
        fields_by_kind: dict = {}
        for f in out["fields"]:
            fields_by_kind.setdefault(f["kind"], []).append(f["name"])
        self.assertIn("userId", fields_by_kind.get("input", []))
        self.assertIn("save", fields_by_kind.get("output", []))
        self.assertIn("user", fields_by_kind.get("ctor-param", []))
        self.assertIn("store", fields_by_kind.get("ctor-param", []))
        # Ctor-param type carried through.
        user_param = next(f for f in out["fields"] if f["name"] == "user")
        self.assertEqual(user_param["type"], "UserService")

    def test_ts_class_methods_skip_private_and_lifecycle(self) -> None:
        out = self.engine.inspect_class("ProfileComponent")
        assert out is not None
        names = {m["name"] for m in out["methods"]}
        self.assertIn("refresh", names)
        self.assertIn("doExport", names)
        # Lifecycle and private skipped.
        self.assertNotIn("ngOnInit", names)
        self.assertNotIn("ngOnDestroy", names)
        self.assertNotIn("_internal", names)


if __name__ == "__main__":
    unittest.main()
