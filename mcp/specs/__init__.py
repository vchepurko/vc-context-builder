"""JSON-Schema descriptors for every MCP tool surfaced over the wire.

Public surface: ``tool_specs() -> List[Dict[str, Any]]`` — concatenates
the per-domain spec lists from this package. Pure data — no dispatch,
no engine references — so the spec set can be inspected / diffed /
snapshot-tested in isolation.

Adding a tool: append a record to the matching domain module
(:mod:`mcp.specs.symbols` for symbol-centric, :mod:`mcp.specs.project`
for everything else, :mod:`mcp.specs.angular` for `ng_*`) AND wire a
handler in :mod:`mcp.dispatcher`. The parity test in
``tests/test_mcp_server.py`` enforces both halves stay in sync.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from . import angular, docs, project, symbols


def tool_specs(disabled: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
    """Concatenated spec list across every domain module.

    Pass ``disabled`` (a set of tool names) to omit those entries from
    the ``tools/list`` response so the client never sees them at all.
    """
    out: List[Dict[str, Any]] = []
    out.extend(symbols.specs())
    out.extend(project.specs())
    out.extend(angular.specs())
    out.extend(docs.specs())
    if disabled:
        out = [t for t in out if t["name"] not in disabled]
    return out


__all__ = ["tool_specs"]
