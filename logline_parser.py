"""Parse a Python ``logging`` line into structured info.

Recognises the canonical text layout produced by Python's standard
``logging.StreamHandler`` with formats like
``"%(asctime)s [%(levelname)s] %(name)s: %(message)s"``.

Maps a dotted logger name (``bot.handlers.admin_staff``) to the
project file (``bot/handlers/admin_staff.py``) when one exists. If the
message starts with an identifier and that identifier matches a known
symbol in ``agent_symbols.json``, folds in its file/line/role too —
giving a one-call mapping from "log line in Datadog/Loki" to "go to
this file:line in the editor".

Project-agnostic. Stdlib only.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

# Common Python logging line shapes we accept. Built-in formatters write:
#
#   2026-05-04 19:43:27,979 [INFO] bot.middlewares.diag_message: msg
#   INFO  [alembic.runtime.migration] msg              # legacy fileConfig
#   INFO:     Started server process [62]              # uvicorn default
#
# We only target the FIRST shape — the structured one — because the
# others are tool-specific and don't carry a logger name we can use
# to map to a file. Empty result for unrecognised formats is fine.

_LINE_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+)?
    \[(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\]\s+
    (?P<logger>[\w][\w\.]*)
    :\s+
    (?P<msg>.+)
    $
    """,
    re.VERBOSE,
)

# Identifier pattern — captures the leading identifier of the message
# body. Most code emits log lines like ``log.info("symbol_name fired")``
# so the first token IS the function/method name.
_IDENT_RE = re.compile(r"^[A-Za-z_][\w]*")


def logline_to_symbol(
    project_root: str,
    line: str,
    symbols: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Parse ``line``; return a dict describing what we know.

    ``matched=False`` when the line shape isn't recognised — caller can
    decide whether to surface it as ``unknown format`` or fall through.

    When ``symbols`` is provided (typically the loaded
    ``agent_symbols.json``), the leading identifier in the message is
    looked up there; if a match exists, ``symbol`` / ``symbol_file`` /
    ``role`` join the result.
    """
    if not line:
        return {"matched": False, "raw": ""}

    match = _LINE_RE.match(line)
    if not match:
        return {"matched": False, "raw": line.strip()}

    logger_name = match.group("logger")
    message = match.group("msg")

    # Map dotted logger to a project-relative file path. Try the
    # straight ``a.b.c → a/b/c.py`` form; if that file exists, use it.
    # Don't search for shorter prefixes — Python convention is one
    # logger per module via ``logging.getLogger(__name__)``.
    candidate_rel = logger_name.replace(".", "/") + ".py"
    candidate_full = os.path.join(project_root, candidate_rel)
    file_rel: Optional[str] = candidate_rel if os.path.isfile(candidate_full) else None

    # Try to extract the first identifier of the message — typically
    # the symbol that emitted the log line.
    ident_match = _IDENT_RE.match(message)
    first_ident = ident_match.group(0) if ident_match else None

    out: Dict[str, Any] = {
        "matched": True,
        "level": match.group("level"),
        "logger": logger_name,
        "file": file_rel,
        "message": message,
    }
    if match.group("ts"):
        out["timestamp"] = match.group("ts")

    if first_ident:
        if symbols and first_ident in symbols:
            sym = symbols[first_ident]
            if isinstance(sym, dict):
                out["symbol"] = first_ident
                if sym.get("file"):
                    out["symbol_file"] = sym["file"]
                if sym.get("role"):
                    out["role"] = sym["role"]
        else:
            out["symbol_hint"] = first_ident

    return out
