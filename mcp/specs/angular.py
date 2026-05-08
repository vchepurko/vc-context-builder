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
                "For an Angular @Injectable service, list call sites "
                "across components / services / guards (heuristic: "
                "constructor params and `inject(Service)` calls in "
                "scrubbed bodies). Returns [{file, line, kind}] where "
                "kind is 'constructor' or 'inject'. Confirm by reading "
                "the source — this is a substring scan, not a full TS "
                "type-resolver."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": "Service class name.",
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
    ]
