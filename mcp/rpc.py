"""JSON-RPC 2.0 framing helpers + ``handle_request`` translator.

Tiny wrappers around dict construction so the wire format stays
consistent across the codebase. ``handle_request`` is the per-frame
entry point: takes one parsed JSON object + a :class:`~mcp.dispatcher
.Dispatcher` and returns the response object (or ``None`` for
notifications).

Constants here so other modules can import them without pulling the
specs / dispatcher dependency tree.
"""

from __future__ import annotations

import json
import traceback
from typing import Any, Dict, Optional

from .dispatcher import Dispatcher
from .specs import tool_specs

SERVER_NAME = "vc-context"
# Loose SemVer; bump minor on user-visible MCP / CLI surface changes
# (new tools, new artefact fields). Major bump reserved for breaking
# tool removal or artefact-shape changes. Patch for fixes.
SERVER_VERSION = "0.5.0"
PROTOCOL_VERSION = "2024-11-05"


def ok(req_id: Any, result: Any) -> Dict[str, Any]:
    """Build a JSON-RPC success response."""
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def err(req_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    """Build a JSON-RPC error response."""
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


def content_text(payload: Any) -> Dict[str, Any]:
    """Wrap a Python object as MCP tool content (single text block)."""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    return {"content": [{"type": "text", "text": text}]}


def handle_request(
    req: Dict[str, Any],
    dispatcher: Dispatcher,
) -> Optional[Dict[str, Any]]:
    """Translate one JSON-RPC request into a response.

    Returns ``None`` for notifications (requests without an ``id``) —
    the MCP transport expects those to be silent.
    """
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    # Notifications carry no id; reply silently.
    is_notification = "id" not in req

    if method == "initialize":
        result: Dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        return None if is_notification else ok(req_id, result)

    if method == "tools/list":
        return None if is_notification else ok(req_id, {"tools": tool_specs()})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str):
            return err(req_id, -32602, "Missing tool name")
        try:
            value = dispatcher.call(name, args)
        except FileNotFoundError as exc:
            payload = {"error": "missing_artifact", "detail": str(exc)}
            result = content_text(payload)
            result["isError"] = True
            return ok(req_id, result)
        except Exception as exc:  # pragma: no cover — surface as MCP error
            return err(req_id, -32000, f"Tool failed: {exc}", traceback.format_exc())
        return ok(req_id, content_text(value))

    if method in ("ping",):
        return None if is_notification else ok(req_id, {})

    if isinstance(method, str) and method.startswith("notifications/"):
        # Tolerant no-op for client-initiated notifications.
        return None

    if is_notification:
        return None
    return err(req_id, -32601, f"Method not found: {method}")
