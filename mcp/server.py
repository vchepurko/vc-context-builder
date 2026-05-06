"""Stdio loop + ``main`` argparse entry for the MCP server.

Reads line-delimited JSON-RPC frames forever, dispatches each through
:func:`mcp.rpc.handle_request`, writes the response back. Supports
both single requests and JSON arrays (batch).

Exit codes: 0 on EOF (normal shutdown), non-zero only when ``main``
fails to parse argv.
"""

from __future__ import annotations

import json
import os
import sys
from typing import List, Optional

from .dispatcher import Dispatcher
from .rpc import err, handle_request

# Same sys.path trick as dispatcher.py — allow ``from query_engine
# import QueryEngine`` to resolve when this module is imported as
# part of the ``mcp`` package.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from query_engine import QueryEngine  # noqa: E402


def serve(project_root: str, stdin=None, stdout=None) -> int:
    """Read line-delimited JSON-RPC frames forever.

    Uses unbuffered writes so the host process sees each response as
    soon as it's produced.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    engine = QueryEngine(project_root)
    dispatcher = Dispatcher(engine)

    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            response = err(None, -32700, f"Parse error: {exc}")
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
            continue

        # Allow batched requests (a JSON array of frames).
        if isinstance(req, list):
            responses = [
                handle_request(item, dispatcher) for item in req
                if isinstance(item, dict)
            ]
            responses = [r for r in responses if r is not None]
            if responses:
                stdout.write(json.dumps(responses) + "\n")
                stdout.flush()
            continue

        if not isinstance(req, dict):
            response = err(None, -32600, "Invalid request: expected object")
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
            continue

        response = handle_request(req, dispatcher)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Parse ``--root <path>`` (default cwd), then run :func:`serve`."""
    argv = argv if argv is not None else sys.argv[1:]
    project_root = os.getcwd()
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            project_root = argv[i + 1]
            i += 2
            continue
        i += 1
    return serve(project_root)
