"""Stdio MCP server package for the vc-context query engine.

Public surface:

* :data:`SERVER_NAME` / :data:`SERVER_VERSION` / :data:`PROTOCOL_VERSION`
* :func:`serve` — line-delimited JSON-RPC over stdio.
* :func:`main` — argparse entry, used by ``mcp_server.py``.
* :func:`tool_specs` — JSON-Schema descriptors (read-only).
* :class:`Dispatcher` — handler dispatch (used by tests).
* :func:`handle_request` — translate one frame to one response.

Internal split:

* :mod:`mcp.specs`      — ``tool_specs()`` (one record per MCP tool).
* :mod:`mcp.dispatcher` — ``Dispatcher`` class wiring tool name → engine.
* :mod:`mcp.rpc`        — JSON-RPC framing helpers (``_ok``, ``_err``,
                          ``_content_text``) + ``handle_request``.
* :mod:`mcp.server`     — ``serve`` stdio loop + ``main``.

Backwards compat: ``mcp_server.py`` keeps re-exporting the public
surface so older configs (``python3 .ai-context/mcp_server.py
--root ...``) and tests that import from ``mcp_server`` keep
working.
"""

from __future__ import annotations

from .dispatcher import Dispatcher
from .rpc import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    content_text,
    err,
    handle_request,
    ok,
)
from .server import main, serve
from .specs import tool_specs

__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "Dispatcher",
    "content_text",
    "err",
    "handle_request",
    "main",
    "ok",
    "serve",
    "tool_specs",
]
