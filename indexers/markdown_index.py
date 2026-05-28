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
    """Write the index to ``agent_docs_index.json`` under
    ``<project_root>/.vc-context/index/`` (or ``out_path`` when given).
    Returns the absolute path."""
    if out_path is None:
        from paths import ensure_index_dir, index_path

        ensure_index_dir(project_root)
        out_path = index_path(project_root, "agent_docs_index.json")
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
    header_pattern: Optional[str] = None,
    *,
    fuzzy: bool = True,
    number: Optional[int] = None,
    heading: Optional[str] = None,
    anchor: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Locate one section in ``file`` by one of four selectors.

    Selector priority (first non-None wins):

    1. ``anchor`` — case-insensitive **prefix** match on the section
       slug. Pass ``"31"`` to find ``31-unified-user`` (covers the
       common "I know the number, not the full slug" case).
    2. ``number`` — match a numeric heading prefix, e.g.
       ``number=31`` finds ``## 31. Something``.
    3. ``heading`` — case-insensitive **substring** match on the
       heading text, ranked shortest-heading-first (so a 4-word
       heading beats a 40-word one for the same query).
    4. ``header_pattern`` — back-compat path. With ``fuzzy=True``
       (default) it's a case-insensitive substring (first match
       wins, top-down). With ``fuzzy=False`` it's an exact
       case-insensitive equality.

    Returns ``{level, text, line, end_line, anchor}`` or ``None``.
    """
    sections = get_toc(index, file) or []

    if anchor is not None:
        needle = anchor.strip().lower()
        if not needle:
            return None
        for sec in sections:
            sec_anchor = (sec.get("anchor") or "").lower()
            if sec_anchor.startswith(needle):
                return dict(sec)
        return None

    if number is not None:
        prefix_re = re.compile(rf"^\s*{re.escape(str(number))}(?:\.|\s|$)")
        for sec in sections:
            text = sec.get("text") or ""
            if prefix_re.match(text):
                return dict(sec)
        return None

    if heading is not None:
        needle = heading.strip().lower()
        if not needle:
            return None
        matches = [sec for sec in sections if needle in sec["text"].lower()]
        if not matches:
            return None
        matches.sort(key=lambda s: len(s["text"]))
        return dict(matches[0])

    if header_pattern is not None:
        needle = header_pattern.strip().lower()
        if not needle:
            return None
        for sec in sections:
            text_lower = sec["text"].lower()
            if (fuzzy and needle in text_lower) or (not fuzzy and needle == text_lower):
                return dict(sec)
        return None

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


def _find_containing_section(
    sections: List[Dict[str, Any]],
    line: int,
) -> Optional[Dict[str, Any]]:
    """Pick the deepest section whose ``[line, end_line]`` range contains
    ``line``. Returns ``{heading, anchor, level}`` or ``None`` when no
    section contains the line (e.g. preamble before the first heading).
    """
    best: Optional[Dict[str, Any]] = None
    for sec in sections:
        sl = sec.get("line")
        sel = sec.get("end_line")
        if not isinstance(sl, int) or not isinstance(sel, int):
            continue
        if sl <= line <= sel:
            # "Deepest" = highest level (## inside ## is more specific).
            if best is None or sec.get("level", 0) >= best.get("level", 0):
                best = sec
    if best is None:
        return None
    return {
        "heading": best.get("text"),
        "anchor": best.get("anchor"),
        "level": best.get("level"),
    }


def search_doc_text(
    project_root: str,
    index: Dict[str, Any],
    query: str,
    *,
    file: Optional[str] = None,
    regex: bool = False,
    case_sensitive: bool = False,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """Markdown-aware grep across indexed docs — like ``find_xref``,
    but each hit carries its **containing section** so the agent sees
    "this mention is inside Phase 2 of IDEAS #28" without a follow-up
    ``read_slice``.

    Returns ``[{file, line, snippet, section: {heading, anchor, level}}]``
    capped at ``max_results``. ``section`` is ``None`` for matches in
    the preamble before any heading.

    Args:
      query: Substring (default) or Python regex when ``regex=True``.
      file: Optional repo-relative path to scope the search to one doc.
      regex: Treat ``query`` as a regex instead of a literal substring.
      case_sensitive: Default ``False``.
      max_results: Cap (default 50).

    Closes the "which docs mention X" free-text query class that
    today drops to Bash ``grep -rln`` because ``find_xref`` doesn't
    attach section context.
    """
    docs = index.get("docs", {})
    targets: List[str]
    if file:
        targets = [file] if file in docs else []
    else:
        targets = list(docs.keys())

    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(query, flags)

        def matches(line: str) -> bool:
            return pattern.search(line) is not None
    else:
        needle = query if case_sensitive else query.lower()

        def matches(line: str) -> bool:
            hay = line if case_sensitive else line.lower()
            return needle in hay

    out: List[Dict[str, Any]] = []
    for rel in targets:
        if len(out) >= max_results:
            break
        sections = docs.get(rel, {}).get("sections") or []
        full = os.path.join(project_root, rel)
        try:
            with open(full, encoding="utf-8") as fh:
                for i, line in enumerate(fh, start=1):
                    if not matches(line):
                        continue
                    snippet = line.rstrip()[:200]
                    out.append(
                        {
                            "file": rel,
                            "line": i,
                            "snippet": snippet,
                            "section": _find_containing_section(sections, i),
                        }
                    )
                    if len(out) >= max_results:
                        break
        except OSError:
            continue
    return out


def link_graph(project_root: str, index: Dict[str, Any]) -> Dict[str, Any]:
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
            resolved = os.path.normpath(os.path.join(src_dir, target_path)).replace(os.sep, "/")
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


def extract_section_bodies(
    project_root: str,
    docs_index: Dict[str, Any],
    *,
    max_body_chars: int = 600,
) -> List[Dict[str, Any]]:
    """Return a list of section dicts ready for embedding.

    Each dict has: id, file, title, anchor, level, line_start, line_end,
    search_text (heading + truncated body).

    ``max_body_chars`` truncates the body so very long sections don't
    produce oversized embedding inputs.
    """
    out: List[Dict[str, Any]] = []
    docs = docs_index.get("docs") or {}
    for rel, meta in docs.items():
        sections = meta.get("sections") or []
        if not sections:
            continue
        full_path = os.path.join(project_root, rel)
        try:
            with open(full_path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for sec in sections:
            line_start = sec.get("line")
            line_end = sec.get("end_line")
            title = sec.get("text") or ""
            anchor = sec.get("anchor") or ""
            level = sec.get("level") or 1
            if not title or not isinstance(line_start, int):
                continue
            # Body = lines after the heading until end_line (exclusive of
            # the next heading line). Skip the heading line itself.
            body_lines: List[str] = []
            body_start = line_start  # 1-indexed → index line_start (0-indexed)
            body_end = line_end if isinstance(line_end, int) else len(lines)
            for raw in lines[body_start:body_end]:
                stripped = raw.strip()
                if stripped.startswith("#"):
                    break  # hit next heading inside range
                if stripped:
                    body_lines.append(stripped)
            body = " ".join(body_lines)
            if len(body) > max_body_chars:
                body = body[:max_body_chars] + "…"
            search_text = f"{title}\n{body}".strip() if body else title
            # Include line_start to guarantee uniqueness even when two
            # sections share the same anchor (duplicate headings).
            sec_id = f"{rel}#{anchor}@{line_start}" if anchor else f"{rel}#L{line_start}"
            out.append(
                {
                    "id": sec_id,
                    "file": rel,
                    "title": title,
                    "anchor": anchor or None,
                    "level": level,
                    "line_start": line_start,
                    "line_end": line_end,
                    "search_text": search_text,
                }
            )
    return out
