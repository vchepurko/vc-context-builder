"""Thin entry point for the stdio MCP server.

Kept at the submodule root for backwards compatibility with
existing setups that wire MCP via
``python3 .ai-context/mcp_server.py --root <project>`` (this is the
shape ``install.sh`` and ``MCP_SETUP.md`` advertise).

The real implementation lives in the :mod:`mcp` package. This file
re-exports the public surface so legacy imports
(``from mcp_server import _tool_specs, _Dispatcher``) keep working
while new code uses ``from mcp import tool_specs, Dispatcher``.

Reference: https://spec.modelcontextprotocol.io/specification/
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from mcp import (  # noqa: E402
    Dispatcher,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    content_text,
    err,
    handle_request,
    main,
    ok,
    serve,
    tool_specs,
)

# ----------------------------------------------------------------------
# Backwards-compat aliases.
#
# Older tests / external scripts import the underscore-prefixed
# (private-by-convention) names that lived here when this file was a
# single 1100-line module. Keep the aliases until those callers are
# updated; they delegate to the new public names in :mod:`mcp`.
# ----------------------------------------------------------------------

_Dispatcher = Dispatcher
_tool_specs = tool_specs
_ok = ok
_err = err
_content_text = content_text


__all__ = [
    "Dispatcher",
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "_Dispatcher",
    "_content_text",
    "_err",
    "_ok",
    "_tool_specs",
    "content_text",
    "err",
    "handle_request",
    "main",
    "ok",
    "serve",
    "tool_specs",
]


if __name__ == "__main__":
    raise SystemExit(main())
