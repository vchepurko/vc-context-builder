"""Angular-specific MCP tool specs.

Component / route / inject-graph queries kept apart from the core
project surface so non-Angular projects can audit the spec list
without paging through irrelevant ng_* tools.
"""

from __future__ import annotations

from typing import Any, Dict, List


def specs() -> List[Dict[str, Any]]:
    return [
        {
            "name": "ng_audit_component",
            "description": (
                "Composite audit of a single Angular @Component. Returns "
                "{name, file, role, selector, template_url, standalone, "
                "inputs, outputs, style_urls, test} — everything indexed "
                "for the class without making the caller stitch four "
                "separate find_symbol / find_test / find_in_templates "
                "calls. Use this before any component-level refactor."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Component class name (case-sensitive).",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "ng_uses_selector",
            "description": (
                "Find every HTML template that uses an Angular selector "
                "(e.g. 'app-cart-item' or 'mat-button'). Wraps "
                "find_in_templates with two passes — `<selector` for "
                "elements and `[selector]` for attribute directives — "
                "deduped by (file, line). Returns [{file, line, text}] "
                "capped at 100."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Selector without surrounding brackets.",
                    },
                    "match_path": {
                        "type": "string",
                        "description": "Optional fnmatch glob to scope.",
                    },
                },
                "required": ["selector"],
            },
        },
        {
            "name": "ng_overview",
            "description": (
                "One-call zero-arg snapshot of Angular surface: counts "
                "for ng-component / ng-service / ng-module / ng-pipe / "
                "ng-directive / ng-guard, plus a `standalone_components` "
                "count and the list of detected `providers_root` "
                "(services with `providedIn: 'root'`). Cheap — reads "
                "agent_symbols.json once."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "ng_inject_graph",
            "description": (
                "DI injection lookup — two modes, auto-detected:\n\n"
                "**Service mode** (pass a service class name): returns "
                "every file where that service is injected via "
                "constructor DI or `inject(ServiceName)`. "
                "Result: [{file, line, kind, service}] where kind is "
                "'constructor' or 'inject'.\n\n"
                "**Module mode** (pass an NgModule class name): returns "
                "ALL injection points within that module's source tree — "
                "every `inject(X)` and `constructor(... : X)` across all "
                "components/services in the module folder. Each record "
                "includes a `service` field with the injected type name. "
                "Use this to see the full DI graph of a feature module.\n\n"
                "Heuristic substring scan — not a full TS type-resolver. "
                "Returns [] for unknown names."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": (
                            "Service class name (service mode) or "
                            "NgModule class name (module mode). "
                            "Auto-detected."
                        ),
                    },
                },
                "required": ["service"],
            },
        },
        {
            "name": "ng_list_routes",
            "description": (
                "Every Angular route extracted from "
                "RouterModule.forRoot/forChild/provideRouter blocks. "
                "Each record: {path, component, file, line, lazy, "
                "redirect_to, guards}. Empty on non-Angular projects."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "ng_route_for_path",
            "description": (
                "Resolve an Angular URL path (e.g. 'users/:id' or "
                "'/admin') to its route record(s). Exact match first, "
                "falls back to substring so 'users' finds both "
                "'users/:id' and 'admin/users'. Strips a leading "
                "slash so either form works."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "ng_routes_for_component",
            "description": (
                "Reverse lookup — every Angular route whose "
                "`component` field equals the given class name. "
                "Useful for 'where is HomeComponent mounted?' before "
                "renaming or moving the file."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "ng_eslint_violations",
            "description": (
                "Run ESLint on a project-relative path (default: 'src') "
                "and return structured violations as "
                "[{file, line, col, severity, rule, message}]. "
                "Uses the project's own ESLint config via `npx eslint "
                "--format json`. Replaces the Python-only "
                "`lint_violations` for TypeScript/Angular projects. "
                "Capped at `max_results` (default 100)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project-relative path to lint (default: 'src').",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Cap on returned violations (default 100, max 500).",
                    },
                },
            },
        },
        {
            "name": "ng_find_module",
            "description": (
                "Find which NgModule declares a given Angular symbol "
                "(component, directive, pipe). Scans every ng-module "
                "file for a word-boundary match. Returns "
                "{module, file} for the first match, or null. "
                "Useful for projects with many NgModules — avoids "
                "grepping declarations arrays manually."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Angular class name to locate (e.g. 'ProfileEditGroupsComponent')."
                        ),
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "ng_ts_class_shape",
            "description": (
                "Return the public shape of a TypeScript class: "
                "constructor_params (name + type for each DI param), "
                "inputs (@Input properties), outputs (@Output properties), "
                "and public_methods. "
                "Replaces the Python-only `inspect_class` for "
                "Angular/TypeScript code. Returns null when the symbol "
                "is not indexed."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "TypeScript class name (case-sensitive).",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "ng_ajs_find",
            "description": (
                "Find an AngularJS symbol definition in the legacy app/ tree. "
                "Searches for .component(), .service(), .directive(), "
                ".filter(), .factory(), .controller() registrations by name. "
                "Three-pass lookup:\n"
                "1. Exact case-sensitive match → {name, kind, file, line}.\n"
                "2. Case-insensitive fallback — handles 'myService' vs 'MyService' "
                "mismatches. Returns canonical name from file + queried_as field.\n"
                "3. Not found → {found: false, note, suggestions:[{name, kind, file, line}]} "
                "with up to 5 closest AJS registrations by substring/prefix similarity. "
                "Never returns null — always actionable.\n\n"
                "Use this instead of find_symbol for AJS symbols — "
                "find_symbol has a 54%+ miss rate on the app/ directory."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "AngularJS symbol name as registered (e.g. 'userProfileMenu'). "
                            "Case-insensitive fallback is applied automatically."
                        ),
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "ng_module_members",
            "description": (
                "Return the declarations, imports, exports, and providers "
                "of an NgModule as lists of identifier strings. "
                "Returns {module, file, declarations, imports, exports, providers}. "
                "Essential for projects with many NgModules (this project has 43) "
                "and zero standalone components — every component lives in some module."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "NgModule class name (e.g. 'MyProfileModule').",
                    },
                },
                "required": ["name"],
            },
        },
    ]
