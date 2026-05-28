"""Tests for the Angular RouterModule path → component bridge.

Covers the four shapes that produce most real-world Angular routing
configs: ``RouterModule.forRoot``, ``RouterModule.forChild``,
``provideRouter`` (standalone), and a bare ``const X: Routes = [...]``
declaration.

Then end-to-end via ``build_ng_route_index`` over a synthetic project
tree, verifying the artifact shape and the read-side queries
(``route_for_path`` / ``routes_for_component``).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from indexers.ng_route_bridge import (
    _balance_array,
    _extract_route_record,
    _routes_in_file,
    build_ng_route_index,
    route_for_path,
    routes_for_component,
)


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


# ----------------------------------------------------------------------
# Low-level helpers
# ----------------------------------------------------------------------


class BalanceArrayTests(unittest.TestCase):
    def test_simple_balance(self) -> None:
        text = "stuff [a, b, c] tail"
        self.assertEqual(_balance_array(text, 6), 14)

    def test_nested_arrays_balance(self) -> None:
        text = "[1, [2, 3], 4]"
        self.assertEqual(_balance_array(text, 0), 13)

    def test_string_brackets_dont_count(self) -> None:
        text = "[1, '[fake]', 2]"
        self.assertEqual(_balance_array(text, 0), 15)

    def test_unbalanced_returns_minus_one(self) -> None:
        self.assertEqual(_balance_array("[1, 2", 0), -1)


class ExtractRouteRecordTests(unittest.TestCase):
    def _record(self, body: str) -> dict:
        return _extract_route_record(body, file_rel="x.ts", line=1)

    def test_minimal_path_component(self) -> None:
        rec = self._record("{ path: 'home', component: HomeComponent }")
        self.assertEqual(rec["path"], "home")
        self.assertEqual(rec["component"], "HomeComponent")
        self.assertFalse(rec["lazy"])
        self.assertEqual(rec["guards"], [])
        self.assertIsNone(rec["redirect_to"])

    def test_lazy_loaded_route(self) -> None:
        rec = self._record(
            "{ path: 'admin', loadChildren: () => import('./admin').then(m => m.AdminModule) }"
        )
        self.assertEqual(rec["path"], "admin")
        self.assertIsNone(rec["component"])
        self.assertTrue(rec["lazy"])

    def test_redirect_route(self) -> None:
        rec = self._record("{ path: 'old', redirectTo: '/new', pathMatch: 'full' }")
        self.assertEqual(rec["redirect_to"], "/new")
        self.assertIsNone(rec["component"])

    def test_guards_extracted(self) -> None:
        rec = self._record(
            "{ path: 'me', component: ProfileComponent, canActivate: [AuthGuard, RoleGuard] }"
        )
        self.assertEqual(rec["guards"], ["AuthGuard", "RoleGuard"])

    def test_object_without_path_returns_none(self) -> None:
        # Config blob (data, resolve, etc.) accidentally captured by the
        # array walker — must NOT produce a route record.
        rec = self._record("{ data: { title: 'X' } }")
        self.assertIsNone(rec)


# ----------------------------------------------------------------------
# Per-file scan
# ----------------------------------------------------------------------


class RoutesInFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="vc-ng-routes-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _scan(self, content: str, name: str = "routes.ts") -> list[dict]:
        path = os.path.join(self.tmp, name)
        _write(path, content)
        return _routes_in_file(path, self.tmp)

    def test_for_root_array(self) -> None:
        out = self._scan(
            "RouterModule.forRoot([\n"
            "  { path: '', component: HomeComponent },\n"
            "  { path: 'about', component: AboutComponent },\n"
            "])\n"
        )
        self.assertEqual(len(out), 2)
        self.assertEqual([r["path"] for r in out], ["", "about"])

    def test_for_child_array(self) -> None:
        out = self._scan("RouterModule.forChild([{ path: 'inner', component: InnerComponent }])\n")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["path"], "inner")

    def test_provide_router_standalone(self) -> None:
        out = self._scan("provideRouter([{ path: 'feed', component: FeedComponent }])\n")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["component"], "FeedComponent")

    def test_bare_routes_const(self) -> None:
        out = self._scan(
            "const APP_ROUTES: Routes = [\n"
            "  { path: 'a', component: A },\n"
            "  { path: 'b', component: B },\n"
            "]\n"
        )
        self.assertEqual(len(out), 2)

    def test_skip_files_without_router_markers(self) -> None:
        out = self._scan("export class Foo {}\n", name="foo.ts")
        self.assertEqual(out, [])

    def test_line_numbers_track(self) -> None:
        out = self._scan(
            "// comment\n"
            "// comment\n"
            "RouterModule.forRoot([\n"
            "  { path: 'home', component: HomeComponent },\n"
            "])\n"
        )
        # The route record's line should point at line 4 (the `{`).
        self.assertEqual(out[0]["line"], 4)


# ----------------------------------------------------------------------
# Project-wide build + read-side queries
# ----------------------------------------------------------------------


class BuildIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="vc-ng-routes-build-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

        _write(
            os.path.join(self.tmp, "src/app/app-routing.module.ts"),
            (
                "import { RouterModule, Routes } from '@angular/router';\n"
                "import { HomeComponent } from './home/home.component';\n"
                "\n"
                "const routes: Routes = [\n"
                "  { path: '', component: HomeComponent },\n"
                "  { path: 'users/:id', component: UserDetailComponent },\n"
                "];\n"
                "\n"
                "@NgModule({ imports: [RouterModule.forRoot(routes)] })\n"
                "export class AppRoutingModule {}\n"
            ),
        )
        # Lazy children registered in a feature module.
        _write(
            os.path.join(self.tmp, "src/app/admin/admin-routing.module.ts"),
            (
                "RouterModule.forChild([\n"
                "  { path: '', component: AdminHomeComponent, "
                "    canActivate: [AuthGuard] },\n"
                "])\n"
            ),
        )
        # node_modules — must be skipped.
        _write(
            os.path.join(self.tmp, "node_modules/foo/dist/r.ts"),
            ("RouterModule.forRoot([{ path: 'noise', component: X }])\n"),
        )

    def test_build_collects_from_all_modules(self) -> None:
        out = build_ng_route_index(self.tmp)
        files = sorted({r["file"] for r in out})
        self.assertIn("src/app/app-routing.module.ts", files)
        self.assertIn("src/app/admin/admin-routing.module.ts", files)
        # node_modules excluded.
        self.assertNotIn("node_modules/foo/dist/r.ts", files)
        # 2 from main + 1 from admin = 3 routes.
        self.assertEqual(len(out), 3)

    def test_route_for_path_exact_then_substring(self) -> None:
        out = build_ng_route_index(self.tmp)
        # Two routes share path '' (root + admin) — both come back.
        exact = route_for_path(out, "")
        components = sorted(r["component"] for r in exact)
        self.assertEqual(components, ["AdminHomeComponent", "HomeComponent"])
        # Substring fallback: 'users' → 'users/:id'.
        loose = route_for_path(out, "users")
        self.assertEqual(len(loose), 1)
        self.assertEqual(loose[0]["component"], "UserDetailComponent")

    def test_routes_for_component(self) -> None:
        out = build_ng_route_index(self.tmp)
        hits = routes_for_component(out, "HomeComponent")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["path"], "")

    def test_unknown_component_returns_empty(self) -> None:
        out = build_ng_route_index(self.tmp)
        self.assertEqual(routes_for_component(out, "GhostComponent"), [])

    def test_records_are_sorted_for_stable_diff(self) -> None:
        """File order matters — agent_ng_routes.json in git history
        should diff cleanly across rebuilds."""
        out = build_ng_route_index(self.tmp)
        sorted_keys = sorted((r["file"], r["line"]) for r in out)
        actual_keys = [(r["file"], r["line"]) for r in out]
        self.assertEqual(actual_keys, sorted_keys)


if __name__ == "__main__":
    unittest.main()
