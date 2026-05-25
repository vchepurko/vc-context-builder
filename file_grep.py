"""Surgical single-file grep.

Fills the gap when an agent knows the file but is hunting for a
string inside it that ``find_symbol`` can't locate — top-level
shape only, large monolith files like ``Checkout.js`` where the
indexer reaches only the outer class. Closes the "I know the file,
I'm hunting a string inside it" case identified in the submodule
ROADMAP gap-closers.

Stdlib only. No persistent index — re-reads the target file each
call (single I/O round-trip, the file is small relative to the
indexer's normal walk).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

_BODY_BYTE_CAP = 5_000_000  # 5 MB — skip larger files to bound work.
_SNIPPET_MAX = 200


def grep_file(
    project_root: str,
    file: str,
    pattern: str,
    *,
    use_regex: bool = False,
    case_sensitive: bool = False,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Return ``[{line, text}]`` for every line in ``file`` matching
    ``pattern``, capped at ``limit``. ``text`` is right-trimmed and
    truncated to ``_SNIPPET_MAX`` chars to keep responses bounded.

    Path is resolved against ``project_root`` and must stay under it
    (no ``..`` escape). Missing files, oversize files (> 5 MB), and
    empty patterns return ``[]``. Invalid regex raises ``re.error``.
    """
    if not pattern or not file:
        return []

    abs_root = os.path.abspath(project_root)
    abs_file = os.path.abspath(os.path.join(abs_root, file))
    try:
        if os.path.commonpath([abs_root, abs_file]) != abs_root:
            return []
    except ValueError:
        # Cross-drive on Windows — treat as escape.
        return []

    try:
        size = os.path.getsize(abs_file)
    except OSError:
        return []
    if size > _BODY_BYTE_CAP:
        return []

    # Auto-promote to regex when the pattern contains regex metacharacters
    # that would be meaningless as a literal (| \b \d \w ^ $).
    # Prevents silent empty results when the caller passes "A|B|C" expecting
    # alternation but forgets use_regex=true (observed 81 % empty rate).
    regex_hints = re.compile(r"[|\\^$*+?{}\[\]()]")
    if not use_regex and regex_hints.search(pattern):
        try:
            re.compile(pattern)  # validate — fallback to literal on bad regex
            use_regex = True
        except re.error:
            pass

    flags = 0 if case_sensitive else re.IGNORECASE
    matcher: Optional[re.Pattern[str]] = re.compile(pattern, flags) if use_regex else None
    needle = pattern if case_sensitive else pattern.lower()

    out: List[Dict[str, Any]] = []
    try:
        with open(abs_file, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, start=1):
                if len(out) >= limit:
                    break
                if matcher is not None:
                    if matcher.search(line) is None:
                        continue
                else:
                    hay = line if case_sensitive else line.lower()
                    if needle not in hay:
                        continue
                text = line.rstrip()
                if len(text) > _SNIPPET_MAX:
                    text = text[:_SNIPPET_MAX] + " …"
                out.append({"line": i, "text": text})
    except OSError:
        return []
    return out
