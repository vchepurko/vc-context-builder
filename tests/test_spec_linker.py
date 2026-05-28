"""Tests for the TS/JS spec linker (Angular ``<name>.spec.ts`` convention).

Builds a tiny synthetic project tree with co-located ``cart.service.ts``
+ ``cart.service.spec.ts`` and exercises the four pieces of the linker:

1. ``_imported_names_from_spec`` — import-binding extraction.
2. ``_spec_blocks`` — describe / it scanner.
3. ``build_spec_reference_index`` — full project sweep.
4. ``find_test_for_symbol`` — end-to-end resolver including co-location
   fallback.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from test_analysis.test_linking import (
    _candidate_spec_files,
    _imported_names_from_spec,
    _spec_blocks,
    _walk_spec_files,
    build_spec_reference_index,
    build_test_index,
    find_test_for_symbol,
)


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class SpecFixtureMixin:
    def _make_ng_project(self) -> str:
        tmp = tempfile.mkdtemp(prefix="vc-spec-")
        self.addCleanup(shutil.rmtree, tmp, True)

        # Source file + co-located spec.
        _write(os.path.join(tmp, "src/app/cart/cart.service.ts"), "export class CartService {}\n")
        _write(
            os.path.join(tmp, "src/app/cart/cart.service.spec.ts"),
            (
                "import { CartService } from './cart.service';\n"
                "import { TestBed } from '@angular/core/testing';\n"
                "import HttpClient from '@angular/common/http';\n"
                "import * as Utils from '../shared/utils';\n"
                "\n"
                "describe('CartService', () => {\n"
                "  it('should compute total', () => {});\n"
                "  it('handles empty cart', () => {});\n"
                "});\n"
            ),
        )

        # A second pair to test multi-file walk.
        _write(
            os.path.join(tmp, "src/app/orders/order-list.component.ts"),
            "export class OrderListComponent {}\n",
        )
        _write(
            os.path.join(tmp, "src/app/orders/order-list.component.spec.ts"),
            (
                "import { OrderListComponent } from './order-list.component';\n"
                "describe('OrderListComponent', () => {\n"
                "  it('renders rows', () => {});\n"
                "});\n"
            ),
        )

        # node_modules ignore guard — must NOT show up in any walk.
        _write(
            os.path.join(tmp, "node_modules/foo/dist/some.spec.ts"),
            "import {Anything} from 'lib';\ndescribe('x',()=>{it('y',()=>{})});\n",
        )
        return tmp


class WalkAndIgnoreTests(SpecFixtureMixin, unittest.TestCase):
    def test_walk_finds_co_located_specs(self) -> None:
        root = self._make_ng_project()
        files = _walk_spec_files(root)
        rels = sorted(os.path.relpath(p, root).replace(os.sep, "/") for p in files)
        self.assertEqual(
            rels,
            [
                "src/app/cart/cart.service.spec.ts",
                "src/app/orders/order-list.component.spec.ts",
            ],
        )

    def test_node_modules_is_ignored(self) -> None:
        root = self._make_ng_project()
        files = _walk_spec_files(root)
        for p in files:
            self.assertNotIn("node_modules", p)


class ImportExtractionTests(unittest.TestCase):
    def test_braced_named_imports(self) -> None:
        names = _imported_names_from_spec("import { A, B as C } from './x';\n")
        self.assertEqual(names, {"A", "C"})

    def test_default_import(self) -> None:
        names = _imported_names_from_spec("import D from 'lib';\n")
        self.assertEqual(names, {"D"})

    def test_default_with_named(self) -> None:
        names = _imported_names_from_spec("import D, { E, F as G } from 'lib';\n")
        self.assertEqual(names, {"D", "E", "G"})

    def test_namespace_import(self) -> None:
        names = _imported_names_from_spec("import * as Utils from '../utils';\n")
        self.assertEqual(names, {"Utils"})


class BlockScanTests(unittest.TestCase):
    def test_describe_it_pairing(self) -> None:
        content = (
            "describe('Outer', () => {\n  it('case A', () => {});\n  it('case B', () => {});\n});\n"
        )
        blocks = _spec_blocks(content)
        labels = [b[0] for b in blocks]
        self.assertEqual(labels, ["Outer :: case A", "Outer :: case B"])

    def test_top_level_it_without_describe(self) -> None:
        content = "it('orphan case', () => {});\n"
        blocks = _spec_blocks(content)
        self.assertEqual(blocks[0][0], "orphan case")

    def test_test_alias_recognised(self) -> None:
        content = "test('jest-style', () => {});\n"
        blocks = _spec_blocks(content)
        self.assertEqual(blocks[0][0], "jest-style")


class CandidateLookupTests(SpecFixtureMixin, unittest.TestCase):
    def test_co_located_candidate_resolves(self) -> None:
        root = self._make_ng_project()
        cands = _candidate_spec_files(root, "src/app/cart/cart.service.ts")
        rels = [os.path.relpath(p, root).replace(os.sep, "/") for p in cands]
        self.assertEqual(rels, ["src/app/cart/cart.service.spec.ts"])

    def test_no_match_returns_empty(self) -> None:
        root = self._make_ng_project()
        cands = _candidate_spec_files(root, "src/app/cart/no-such.ts")
        self.assertEqual(cands, [])


class IndexAndResolveTests(SpecFixtureMixin, unittest.TestCase):
    def test_index_contains_imported_symbols(self) -> None:
        root = self._make_ng_project()
        idx = build_spec_reference_index(root)
        self.assertIn("CartService", idx)
        self.assertIn("OrderListComponent", idx)
        # CartService spec imports 4 names → all 4 should resolve.
        self.assertIn("TestBed", idx)
        self.assertIn("HttpClient", idx)
        self.assertIn("Utils", idx)

    def test_find_test_for_symbol_via_reference(self) -> None:
        root = self._make_ng_project()
        idx = build_spec_reference_index(root)
        result = find_test_for_symbol(
            root,
            "CartService",
            "src/app/cart/cart.service.ts",
            reference_index=idx,
        )
        self.assertIsNotNone(result)
        self.assertEqual(
            result["test_file"],
            "src/app/cart/cart.service.spec.ts",
        )
        # Shortest test_function wins → "CartService :: case A" (first it).
        self.assertTrue(result["test_function"].startswith("CartService"))

    def test_full_build_test_index_links_ts_symbols(self) -> None:
        """Mimic agent_map.py's call: pass agent_symbols-shaped dict
        and check that ng-component / ng-service classes get linked."""
        root = self._make_ng_project()
        symbols = {
            "CartService": {
                "file": "src/app/cart/cart.service.ts",
                "kind": "class",
                "role": "ng-service",
            },
            "OrderListComponent": {
                "file": "src/app/orders/order-list.component.ts",
                "kind": "class",
                "role": "ng-component",
            },
            "Unrelated": {
                "file": "src/app/random.ts",
                "kind": "func",
            },
        }
        idx = build_test_index(root, symbols)
        self.assertIsNotNone(idx["CartService"])
        self.assertIsNotNone(idx["OrderListComponent"])
        self.assertIsNone(idx["Unrelated"])


class CoLocationFallbackTests(SpecFixtureMixin, unittest.TestCase):
    def test_co_location_fires_when_reference_index_misses(self) -> None:
        """Symbol that isn't imported by name in the spec still resolves
        via the co-located ``<name>.spec.ts`` fallback."""
        root = self._make_ng_project()
        # Pretend the reference index didn't find "CartService" — pass
        # an empty dict so only the co-location path runs.
        result = find_test_for_symbol(
            root,
            "CartService",
            "src/app/cart/cart.service.ts",
            reference_index={},
        )
        self.assertIsNotNone(result)
        self.assertEqual(
            result["test_file"],
            "src/app/cart/cart.service.spec.ts",
        )


if __name__ == "__main__":
    unittest.main()
