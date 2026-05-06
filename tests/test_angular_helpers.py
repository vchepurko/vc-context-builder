"""Tests for the Angular helpers (Feature P).

Builds a tiny synthetic Angular project tree (agent_symbols.json with
ng-component / ng-service records, plus an ``app/`` folder of HTML
templates and TS files) and exercises ``ng_audit_component``,
``ng_uses_selector``, ``ng_overview``, and ``ng_inject_graph``.

No real Angular runtime — these tools are pure indices over JSON
artifacts + line scans, so the tests can run on stdlib alone.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from query_engine import QueryEngine  # noqa: E402


def _write(path: str, payload: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(payload, str):
            fh.write(payload)
        else:
            json.dump(payload, fh)


class AngularFixtureMixin:
    def _make_ng_fixture(self) -> str:
        tmp = tempfile.mkdtemp(prefix="vc-context-ng-")
        self.addCleanup(self._cleanup, tmp)

        _write(os.path.join(tmp, "agent_root.json"), {
            "project_root": tmp,
            "modules": [".", "./src/app"],
            "entry_instruction": "...",
            "roles": {
                "ng-component": ["CartItemComponent", "OrderListComponent"],
                "ng-service":   ["CartService", "AuthService"],
                "ng-pipe":      ["MoneyPipe"],
                "ng-directive": ["AutofocusDirective"],
                "ng-guard":     ["AuthGuard"],
            },
        })

        _write(os.path.join(tmp, "agent_symbols.json"), {
            "CartItemComponent": {
                "file": "src/app/cart/cart-item.component.ts",
                "kind": "class",
                "role": "ng-component",
                "ng_selector": "app-cart-item",
                "ng_template_url": "./cart-item.component.html",
                "ng_style_urls": ["./cart-item.component.scss"],
                "ng_standalone": True,
                "inputs": ["item", "currency"],
                "outputs": ["removed", "qtyChanged"],
            },
            "OrderListComponent": {
                "file": "src/app/orders/order-list.component.ts",
                "kind": "class",
                "role": "ng-component",
                "ng_selector": "app-order-list",
                "ng_standalone": False,
                "inputs": ["orders"],
                "outputs": [],
            },
            "CartService": {
                "file": "src/app/cart/cart.service.ts",
                "kind": "class",
                "role": "ng-service",
                "ng_provided_in": "root",
            },
            "AuthService": {
                "file": "src/app/auth/auth.service.ts",
                "kind": "class",
                "role": "ng-service",
                # No providedIn — old-style providers array on a module.
            },
            "AuthGuard": {
                "file": "src/app/auth/auth.guard.ts",
                "kind": "func",
                "role": "ng-guard",
            },
            "MoneyPipe": {
                "file": "src/app/shared/money.pipe.ts",
                "kind": "class",
                "role": "ng-pipe",
                "ng_pipe_name": "money",
            },
            "AutofocusDirective": {
                "file": "src/app/shared/autofocus.directive.ts",
                "kind": "class",
                "role": "ng-directive",
                "ng_selector": "[appAutofocus]",
            },
        })

        # Tests index — only the cart-item has a spec.
        _write(os.path.join(tmp, "agent_tests.json"), {
            "CartItemComponent": {
                "test_file": "src/app/cart/cart-item.component.spec.ts",
                "test_function": "should render item",
                "line": 12,
            },
        })

        # Module map referenced by ng_inject_graph for file scans.
        _write(os.path.join(tmp, "src", "app", "_module_map.json"), {
            "directory": "./src/app",
            "files": {
                "cart/cart.component.ts": {
                    "exports": [{"name": "CartComponent", "kind": "class", "role": "ng-component"}],
                    "dependencies": [],
                },
                "orders/order-list.component.ts": {
                    "exports": [{"name": "OrderListComponent", "kind": "class", "role": "ng-component"}],
                    "dependencies": [],
                },
            },
        })

        # Synthetic TS source files for inject-graph scans.
        _write(
            os.path.join(tmp, "src", "app", "cart", "cart.component.ts"),
            (
                "import { Component, inject } from '@angular/core';\n"
                "import { CartService } from './cart.service';\n"
                "@Component({ selector: 'app-cart' })\n"
                "export class CartComponent {\n"
                "  constructor(private svc: CartService) {}\n"
                "}\n"
            ),
        )
        _write(
            os.path.join(tmp, "src", "app", "orders", "order-list.component.ts"),
            (
                "import { Component, inject } from '@angular/core';\n"
                "import { CartService } from '../cart/cart.service';\n"
                "@Component({ selector: 'app-order-list' })\n"
                "export class OrderListComponent {\n"
                "  private cart = inject(CartService);\n"
                "}\n"
            ),
        )

        # Synthetic HTML templates for ng_uses_selector / find_in_templates.
        _write(
            os.path.join(tmp, "src", "app", "cart", "cart.component.html"),
            (
                '<div class="cart">\n'
                "  <app-cart-item [item]=\"i\" (removed)=\"onRemove(i)\"></app-cart-item>\n"
                "  <button [appAutofocus]=\"true\">Buy</button>\n"
                "</div>\n"
            ),
        )
        _write(
            os.path.join(tmp, "src", "app", "orders", "order-list.component.html"),
            (
                "<section>\n"
                "  <app-cart-item *ngFor=\"let it of items\" [item]=\"it\"></app-cart-item>\n"
                "</section>\n"
            ),
        )
        return tmp

    def _cleanup(self, tmp: str) -> None:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


class NgAuditComponentTests(AngularFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_ng_fixture()
        self.engine = QueryEngine(self.root)

    def test_audit_returns_full_record_for_known_component(self) -> None:
        out = self.engine.ng_audit_component("CartItemComponent")
        self.assertIsNotNone(out)
        self.assertEqual(out["selector"], "app-cart-item")
        self.assertEqual(out["template_url"], "./cart-item.component.html")
        self.assertTrue(out["standalone"])
        self.assertEqual(out["inputs"], ["item", "currency"])
        self.assertEqual(out["outputs"], ["removed", "qtyChanged"])
        # Test record threaded through find_test.
        self.assertEqual(
            out["test"]["test_file"],
            "src/app/cart/cart-item.component.spec.ts",
        )

    def test_audit_returns_none_for_non_component_role(self) -> None:
        # CartService is ng-service — should NOT pass the audit filter.
        self.assertIsNone(self.engine.ng_audit_component("CartService"))

    def test_audit_returns_none_for_unknown_symbol(self) -> None:
        self.assertIsNone(self.engine.ng_audit_component("NopeComponent"))


class NgUsesSelectorTests(AngularFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_ng_fixture()
        self.engine = QueryEngine(self.root)

    def test_element_form_matches(self) -> None:
        hits = self.engine.ng_uses_selector("app-cart-item")
        # 2 templates each with 1 element-form usage.
        self.assertEqual(len(hits), 2)
        files = sorted({h["file"] for h in hits})
        self.assertEqual(files, [
            "src/app/cart/cart.component.html",
            "src/app/orders/order-list.component.html",
        ])

    def test_attribute_directive_form_matches(self) -> None:
        hits = self.engine.ng_uses_selector("appAutofocus")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["file"], "src/app/cart/cart.component.html")

    def test_unused_selector_returns_empty(self) -> None:
        self.assertEqual(self.engine.ng_uses_selector("ghost-selector"), [])


class NgOverviewTests(AngularFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_ng_fixture()
        self.engine = QueryEngine(self.root)

    def test_overview_counts_all_roles(self) -> None:
        out = self.engine.ng_overview()
        counts = out["counts"]
        self.assertEqual(counts["ng-component"], 2)
        self.assertEqual(counts["ng-service"], 2)
        self.assertEqual(counts["ng-pipe"], 1)
        self.assertEqual(counts["ng-directive"], 1)
        self.assertEqual(counts["ng-guard"], 1)
        # Standalone count + providers_root.
        self.assertEqual(out["standalone_components"], 1)
        self.assertEqual(out["providers_root"], ["CartService"])


class NgInjectGraphTests(AngularFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self._make_ng_fixture()
        self.engine = QueryEngine(self.root)

    def test_finds_constructor_and_inject_call_sites(self) -> None:
        hits = self.engine.ng_inject_graph("CartService")
        kinds = {h["kind"] for h in hits}
        self.assertEqual(kinds, {"constructor", "inject"})
        files = sorted({h["file"] for h in hits})
        self.assertEqual(files, [
            "src/app/cart/cart.component.ts",
            "src/app/orders/order-list.component.ts",
        ])

    def test_unknown_service_returns_empty(self) -> None:
        self.assertEqual(self.engine.ng_inject_graph("UnknownService"), [])

    def test_blank_arg_returns_empty(self) -> None:
        self.assertEqual(self.engine.ng_inject_graph(""), [])


if __name__ == "__main__":
    unittest.main()
