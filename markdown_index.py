"""Markdown index — extract section TOC + link graph from `.md` files.

Closes a real gap surfaced by 2026-05-12 telemetry: ~30% of edits in
that session were on markdown docs (``IDEAS.md``, ``ROADMAP.md``,
``docs/*.md``) and **no MCP tool** indexed that surface — agents fell
back to ``grep "^## "`` + ``Read`` for section navigation.

Artefact shape (``agent_docs_index.json``)::

    {
      "schema": 1,
      "generated_at": "ISO-8601",
      "docs": {
        "<repo-relative path>": {
          "size_bytes": int,
          "top_header": str | None,
          "sections": [
            {"level": 1-6, "text": str, "line": 1-indexed,
             "end_line": int, "anchor": slug}, ...
          ],
          "links": [
            {"text": str, "target": str, "line": int,
             "external": bool}, ...
          ]
        }, ...
      }
    }

End-line of a section is the line BEFORE the next section of equal or
shallower level, or EOF for the last section. That's the standard
heading-scope rule readers expect ("everything under ## Foo" includes
sub-``###`` until the next ``##``).

Code-fence regions are tracked so we don't confuse a ``# comment`` inside
a fenced block with a real markdown heading.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# 1-6 hashes + at least one space + non-empty text. Anchored to start.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# ``[text](target)`` — basic inline-link form. Reference-style links
# (``[text][label]`` + ``[label]: url``) are rarer in our docs and
# add complexity for marginal value; defer until needed.
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Code-fence open/close markers. We track both ```/~~~ to be safe;
# language tag (``` python``) is irrelevant.
_FENCE_RE = re.compile(r"^(```|~~~)")

# Directories to skip during recursive scan — same set ``agent_map``
# already excludes. Kept inline so this module doesn't import from
# the rest of the indexer (it's parallel, not downstream).
_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".github",
        "__pycache__",
        ".venv",
        "node_modules",
        "htmlcov",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)


def _slugify(text: str) -> str:
    """GitHub-style anchor: lowercase, alnum + hyphen, collapse runs."""
    s = text.lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s, flags=re.UNICODE)
    return s.strip("-")


def _iter_md_files(root: str) -> List[str]:
    """Walk ``root`` and yield repo-relative paths to ``.md`` files."""
    out: List[str] = []
    for dirpath, dirs, files in os.walk(root):
        # Mutate in-place to skip subtrees (os.walk contract).
        dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".vc-context")]
        for name in files:
            if not name.endswith(".md"):
                continue
            full = os.path.join(dirpath, name)
            try:
                rel = os.path.relpath(full, root)
            except ValueError:
                continue
            out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def _scan_one(path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse one markdown file into (sections, links).

    Sections include ``end_line`` already computed by a single
    backwards pass over the heading list. Links carry ``external``
    so the link-graph builder can filter to internal-only refs cheaply.
    """
    sections: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return [], []

    in_fence = False
    for i, line in enumerate(lines, start=1):
        if _FENCE_RE.match(line.lstrip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        m_h = _HEADING_RE.match(line)
        if m_h:
            level = len(m_h.group(1))
            text = m_h.group(2).strip()
            sections.append(
                {
                    "level": level,
                    "text": text,
                    "line": i,
                    # Filled in by the backward pass below.
                    "end_line": 0,
                    "anchor": _slugify(text),
                }
            )

        # Links can appear inside paragraphs — we collect every match
        # per line, no de-dup (callers care about file:line context).
        for m_l in _LINK_RE.finditer(line):
            target = m_l.group(2).strip()
            # Strip a "<...>" fragment-only marker like ``[#foo]``;
            # leave query-strings and anchors intact for callers.
            links.append(
                {
                    "text": m_l.group(1).strip(),
                    "target": target,
                    "line": i,
                    "external": _is_external(target),
                }
            )

    # Compute end_line: each section runs until the line BEFORE the
    # next section of equal or shallower level, or EOF for the last
    # section at any given level. Single forward pass with a stack
    # would also work; the simpler O(n²) loop is fine for ~50 sections
    # per file.
    total = len(lines)
    for idx, sec in enumerate(sections):
        end = total  # default: EOF
        for follow in sections[idx + 1 :]:
            if follow["level"] <= sec["level"]:
                end = follow["line"] - 1
                break
        sec["end_line"] = max(end, sec["line"])

    return sections, links


def _is_external(target: str) -> bool:
    """Heuristic: anything with a scheme or that starts with ``//`` is
    external. Local refs and fragment-only links count as internal."""
    if not target:
        return False
    if target.startswith(("http://", "https://", "mailto:", "ftp://", "//")):
        return True
    return False


def _top_header(sections: List[Dict[str, Any]]) -> Optional[str]:
    """First H1 if present; else first heading of any level; else None.

    The "first H1 if present" rule matches what most renderers display
    as the page title — falling back to ``## Subsection`` for files that
    intentionally skip the H1 (e.g. CHANGELOG index pages).
    """
    for sec in sections:
        if sec["level"] == 1:
            return sec["text"]  # type: ignore[no-any-return]
    return sections[0]["text"] if sections else None  # type: ignore[no-any-return]


def build_index(project_root: str) -> Dict[str, Any]:
    """Walk ``project_root``, parse every ``.md`` file, return the
    artefact dict ready for JSON-dump."""
    docs: Dict[str, Any] = {}
    for rel in _iter_md_files(project_root):
        full = os.path.join(project_root, rel)
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        sections, links = _scan_one(full)
        docs[rel] = {
            "size_bytes": size,
            "top_header": _top_header(sections),
            "sections": sections,
            "links": links,
        }
    return {
        "schema": 1,
        # Use ``timezone.utc`` (Python 3.9+) instead of ``datetime.UTC``
        # which is 3.11-only — the submodule targets 3.9 minimum.
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "docs": docs,
    }


def write_index(project_root: str, out_path: Optional[str] = None) -> str:
    """Write the index to ``agent_docs_index.json`` at the project
    root (or ``out_path`` when given). Returns the absolute path."""
    if out_path is None:
        out_path = os.path.join(project_root, "agent_docs_index.json")
    index = build_index(project_root)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)
    return out_path


# ──────────────────────────────────────────────────────────────────
# Query helpers — pure functions over the loaded index dict so the
# MCP layer / CLI / tests share one implementation.
# ──────────────────────────────────────────────────────────────────


def get_toc(
    index: Dict[str, Any],
    file: str,
    *,
    max_level: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Return the section list for ``file`` (repo-relative path).

    None means the file isn't in the index (typo / not a markdown
    file / outside the tracked tree). Empty list means the file is
    indexed but has no headings.

    ``max_level`` (1–6) restricts the TOC to top-level entries. The
    full recursive tree on a long doc like ``IDEAS.md`` is ~12 KB;
    ``max_level=2`` trims it to ~3 KB by dropping ``###``+
    subsections. The default (``None``) keeps the current behaviour
    — return every heading. Closes the "light TOC" follow-up flagged
    in the markdown-nav Phase 2 roadmap entry.
    """
    rec = index.get("docs", {}).get(file)
    if rec is None:
        return None
    sections: List[Dict[str, Any]] = rec.get("sections", [])
    if max_level is None:
        return sections
    return [s for s in sections if s.get("level", 0) <= max_level]


def find_section(
    index: Dict[str, Any],
    file: str,
    header_pattern: str,
    *,
    fuzzy: bool = True,
) -> Optional[Dict[str, Any]]:
    """Locate one section in ``file`` whose heading matches.

    Match rules (``fuzzy=True`` default):
      * case-insensitive substring on the heading text;
      * the first match wins (top-down).

    ``fuzzy=False`` requires an exact case-insensitive equality with
    the heading text — useful for round-trip queries.

    Returns ``{level, text, line, end_line, anchor}`` or None.
    """
    sections = get_toc(index, file) or []
    needle = header_pattern.strip().lower()
    for sec in sections:
        text_lower = sec["text"].lower()
        if (fuzzy and needle in text_lower) or (not fuzzy and needle == text_lower):
            return dict(sec)
    return None


def list_docs(
    index: Dict[str, Any],
    *,
    path_prefix: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Enumerate indexed docs with metadata.

    ``path_prefix`` limits the result to files whose repo-relative
    path startswith the prefix — e.g. ``"docs/"`` for everything
    under that directory. Returned records are stable-sorted by path.
    """
    out: List[Dict[str, Any]] = []
    for path, rec in index.get("docs", {}).items():
        if path_prefix and not path.startswith(path_prefix):
            continue
        out.append(
            {
                "path": path,
                "top_header": rec.get("top_header"),
                "section_count": len(rec.get("sections") or []),
                "size_bytes": rec.get("size_bytes", 0),
            }
        )
    out.sort(key=lambda r: r["path"])
    return out


def find_xref(
    project_root: str,
    index: Dict[str, Any],
    term: str,
    *,
    case_sensitive: bool = False,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """Full-text search across indexed docs.

    Scans the actual file bodies (re-read on each query) rather than
    embedding text in the index — saves disk space and avoids the
    "index gets stale between rebuilds" trap. ~20 files × few KB =
    sub-millisecond on the project's scale.

    Returns ``[{file, line, snippet}]`` capped at ``max_results``.
    Snippet is the matching line trimmed to 200 chars with the
    needle surrounded by markers.
    """
    needle = term if case_sensitive else term.lower()
    out: List[Dict[str, Any]] = []
    for rel in index.get("docs", {}):
        if len(out) >= max_results:
            break
        full = os.path.join(project_root, rel)
        try:
            with open(full, encoding="utf-8") as fh:
                for i, line in enumerate(fh, start=1):
                    hay = line if case_sensitive else line.lower()
                    if needle in hay:
                        snippet = line.rstrip()[:200]
                        out.append({"file": rel, "line": i, "snippet": snippet})
                        if len(out) >= max_results:
                            break
        except OSError:
            continue
    return out


def link_graph(
    project_root: str, index: Dict[str, Any]
) -> Dict[str, Any]:
    """Forward link adjacency + broken-link report.

    Returns::

        {
          "forward": {<file>: [<target>, ...], ...},
          "broken": [{"file": ..., "line": ..., "target": ...}, ...],
          "external_count": int
        }

    Internal links resolve relative to the **directory of the source
    file** (markdown convention). ``./`` and ``../`` are honoured.
    Anchor-only ``#section`` links are recorded under ``forward`` as
    ``"#section"`` but never count as broken (we'd need section-anchor
    cross-referencing for that — out of scope for v1).
    """
    forward: Dict[str, List[str]] = {}
    broken: List[Dict[str, Any]] = []
    external_count = 0

    for src_rel, rec in index.get("docs", {}).items():
        src_dir = os.path.dirname(src_rel)
        out_targets: List[str] = []
        for link in rec.get("links", []):
            target = link["target"]
            if link.get("external"):
                external_count += 1
                continue
            if target.startswith("#"):
                out_targets.append(target)
                continue
            # Strip ``#fragment`` so we resolve the file part only.
            target_path = target.split("#", 1)[0]
            if not target_path:
                # Pure-anchor link (already handled) — defensive.
                out_targets.append(target)
                continue
            resolved = os.path.normpath(
                os.path.join(src_dir, target_path)
            ).replace(os.sep, "/")
            out_targets.append(resolved)
            full = os.path.join(project_root, resolved)
            if not os.path.exists(full):
                broken.append(
                    {
                        "file": src_rel,
                        "line": link["line"],
                        "target": target,
                    }
                )
        forward[src_rel] = out_targets

    return {
        "forward": forward,
        "broken": broken,
        "external_count": external_count,
    }
