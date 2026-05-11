"""Helper for the ``include_tests`` knob on search/query tools.

By convention test files live under ``tests/`` at the project root.
The query tools default to **excluding** them so day-to-day "where is X
used?" answers don't mix production hits with their tests — that's the
common case. Callers who explicitly want to inspect coverage pass
``include_tests=True`` and get the full set back.

A path counts as a test path when it lives under ``tests/`` or under
``.ai-context/tests/`` (submodule's own tests). The match is anchored
at the start of the path so a directory named ``mytests/`` inside an
unrelated module is NOT classified as a test path.

Kept dependency-free on purpose — every query inspector imports this
helper, including ones that run in the MCP server hot path.
"""

from __future__ import annotations

# Normalised path prefixes. The submodule indexes paths with forward
# slashes (POSIX-style) regardless of host OS — see ``file_parser``
# normalisation — so this stays a simple string check.
_TEST_PREFIXES: tuple[str, ...] = (
    "tests/",
    ".ai-context/tests/",
)


def is_test_path(path: str | None) -> bool:
    """Return ``True`` when ``path`` lives under a known tests root.

    Returns ``False`` for ``None`` or empty strings — call sites can
    pass an unresolved file freely.
    """
    if not path:
        return False
    p = path.replace("\\", "/")
    # Strip leading "./" so e.g. "./tests/foo.py" still classifies.
    if p.startswith("./"):
        p = p[2:]
    return p.startswith(_TEST_PREFIXES)


def filter_test_records(
    records: list,
    *,
    include_tests: bool,
    file_key: str = "file",
) -> list:
    """Drop test-path entries from a list of dict records when
    ``include_tests`` is False; pass-through otherwise.

    ``file_key`` lets the call site point at whichever field carries
    the path (most query tools use ``"file"``; a few legacy ones use
    ``"path"`` — keyword arg keeps the call site explicit).
    """
    if include_tests:
        return records
    return [r for r in records if not is_test_path(r.get(file_key))]
