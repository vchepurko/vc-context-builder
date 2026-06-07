"""Shared query engine for the vc-context-builder artifacts.

A single ``QueryEngine`` instance lazy-loads ``agent_root.json``,
``agent_symbols.json``, and the per-folder ``_module_map.json`` files
written by ``agent_map.py``. It exposes a small, RPC-friendly surface
so CLI users and MCP clients never have to load the raw JSON into
their context window.

Stdlib only by design — the builder is zero-dependency, and so is
this layer.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from _query_inspectors import _InspectorsMixin
from _query_routes import _RoutesMixin
from _query_symbols import _QuerySymbolsMixin
from _query_tests import _TestsMixin
from paths import index_read_path as _index_read


class QueryEngine(_QuerySymbolsMixin, _InspectorsMixin, _RoutesMixin, _TestsMixin):
    """Lazy-loading reader over the three artifact tiers.

    Parameters
    ----------
    project_root:
        Absolute or relative path to the directory that holds
        ``agent_root.json`` / ``agent_symbols.json``. Per-folder
        module maps are discovered by walking from this root.

    Notes
    -----
    All loaders are lazy: nothing is read until the first method call
    that needs that tier. Subsequent calls are served from in-memory
    caches, so the engine is safe to keep around for the lifetime of
    a CLI invocation or MCP server process.

    The engine is **read-only** — it never mutates the artifacts. Only
    ``agent_map.py`` writes them.
    """

    ROOT_FILENAME = "agent_root.json"
    SYMBOLS_FILENAME = "agent_symbols.json"
    TESTS_FILENAME = "agent_tests.json"
    ROUTES_FILENAME = "agent_routes.json"
    NG_ROUTES_FILENAME = "agent_ng_routes.json"
    CALLBACKS_FILENAME = "agent_callbacks.json"
    FSM_FLOW_FILENAME = "agent_fsm_flows.json"
    IMPACT_FILENAME = "agent_impact.json"
    TEST_CATEGORIES_FILENAME = "agent_test_categories.json"
    LOCALES_FILENAME = "agent_locale_keys.json"
    DOCS_INDEX_FILENAME = "agent_docs_index.json"
    MAP_FILENAME = "_module_map.json"
    IGNORE_DIRS = frozenset(
        {
            ".git",
            "node_modules",
            "vendor",
            "__pycache__",
            "dist",
            "build",
            ".venv",
            "venv",
            ".idea",
            ".vscode",
        }
    )

    def __init__(self, project_root: str = ".") -> None:
        self.project_root = os.path.abspath(project_root)
        self._root: Optional[Dict[str, Any]] = None
        self._symbols: Optional[Dict[str, Dict[str, Any]]] = None
        # Each entry: (rel_dir, parsed_map_json)
        self._module_maps: Optional[List[Tuple[str, Dict[str, Any]]]] = None
        # Reverse-index for who_calls — built once on demand.
        self._reverse_deps: Optional[Dict[str, List[Dict[str, str]]]] = None
        # Optional per-tier caches for the new artifacts. ``None`` =
        # not yet attempted; ``{}`` = read but artifact absent / empty.
        self._tests: Optional[Dict[str, Any]] = None
        self._routes: Optional[Dict[str, Dict[str, Any]]] = None
        self._ng_routes: Optional[List[Dict[str, Any]]] = None
        self._callbacks: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._fsm_flows: Optional[Dict[str, Dict[str, Any]]] = None
        self._impact: Optional[Dict[str, Any]] = None
        self._test_categories: Optional[Dict[str, Dict[str, Any]]] = None
        self._locale_keys: Optional[Dict[str, Dict[str, Any]]] = None
        self._docs_index: Optional[Dict[str, Any]] = None
        # Memoised ``run_check`` results keyed on (name, args, git_state_hash).
        # Survives across MCP calls in the long-lived server process so
        # repeated ``test-unit`` invocations without source edits return
        # in ~ms instead of re-running pytest.
        self._check_cache: Dict[Tuple[str, Tuple[str, ...], str], Dict[str, Any]] = {}
        # File-content cache for read_slice — keyed by abs_path,
        # value is (mtime, lines). Hot files read 10–20× per session
        # (observed in lms-client Angular work) return from RAM after
        # the first disk read. Mtime check keeps stale entries out.
        # Eviction: when the dict exceeds _FILE_CACHE_MAX entries, the
        # oldest half is dropped (dict insertion order, Python 3.7+).
        self._file_cache: Dict[str, Tuple[float, List[str]]] = {}
        self._FILE_CACHE_MAX = 64
        # Cached conventions.json values — keyed by setting name.
        self._conventions_cache: Optional[Dict[str, Any]] = None

    def _read_convention(self, key: str, default: Any = None) -> Any:
        """Return a value from ``.vc-context/conventions.json``.

        Cached after the first read. Call ``invalidate_caches()`` to
        force a re-read (e.g. after the user edits the file).
        """
        if self._conventions_cache is None:
            conv_path = os.path.join(self.project_root, ".vc-context", "conventions.json")
            try:
                with open(conv_path, encoding="utf-8") as fh:
                    self._conventions_cache = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._conventions_cache = {}
        return self._conventions_cache.get(key, default)

    # ------------------------------------------------------------------
    # Cache invalidation — used after an in-process rebuild so the next
    # query reads fresh artifacts. The engine doesn't watch the disk
    # for changes; callers explicitly drop caches when they've rebuilt
    # the index (e.g. via ``rebuild_index`` MCP tool).
    # ------------------------------------------------------------------

    def invalidate_caches(self) -> None:
        """Drop every lazy-load cache. Forces the next query to re-read
        ``agent_*.json`` from disk."""
        self._root = None
        self._symbols = None
        self._module_maps = None
        self._reverse_deps = None
        self._tests = None
        self._routes = None
        self._ng_routes = None
        self._callbacks = None
        self._fsm_flows = None
        self._impact = None
        self._test_categories = None
        self._locale_keys = None
        self._docs_index = None
        self._file_cache.clear()
        self._conventions_cache = None

    # ------------------------------------------------------------------
    # Lazy loaders
    # ------------------------------------------------------------------

    def _load_root(self) -> Dict[str, Any]:
        if self._root is None:
            path = _index_read(self.project_root, self.ROOT_FILENAME)
            with open(path, encoding="utf-8") as fh:
                self._root = json.load(fh)
        return self._root

    def _load_symbols(self) -> Dict[str, Dict[str, Any]]:
        if self._symbols is None:
            path = _index_read(self.project_root, self.SYMBOLS_FILENAME)
            with open(path, encoding="utf-8") as fh:
                self._symbols = json.load(fh)
        return self._symbols

    def semantic_search(
        self,
        query: str,
        *,
        top_k: int = 5,
        kind: Optional[str] = None,
        role: Optional[str] = None,
        include_tests: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search indexed symbols by semantic text rather than exact name.

        Backed by the local per-repo SQLite store under
        ``~/.vc-context/<repo-hash>/embeddings/``. The store is rebuilt
        lazily if ``agent_symbols.json`` changed since the last build.
        """
        from stores.semantic_store import provider_from_conventions, semantic_search

        return semantic_search(
            self.project_root,
            self._load_symbols(),
            query,
            top_k=top_k,
            kind=kind,
            role=role,
            include_tests=include_tests,
            provider=provider_from_conventions(self.project_root),
        )

    def remember_experience(
        self,
        *,
        context_text: str,
        content: str,
        type: str = "decision",
        source: str = "user",
        source_file: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Persist a repo-local Phase 5 experience."""
        from stores.experience_store import remember_experience

        return remember_experience(
            self.project_root,
            context_text=context_text,
            content=content,
            type=type,
            source=source,
            source_file=source_file,
            confidence=confidence,
        )

    def recall_experience(
        self,
        context: str,
        *,
        top_k: int = 3,
        type: Optional[str] = None,
        min_score: float = 0.05,
    ) -> List[Dict[str, Any]]:
        """Recall repo-local decisions, mistakes, dead ends, and patterns."""
        from stores.experience_store import recall_experience

        return recall_experience(
            self.project_root,
            context,
            top_k=top_k,
            type=type,
            min_score=min_score,
        )

    def _load_tests(self) -> Dict[str, Any]:
        """Return ``agent_tests.json`` content (or ``{}`` if missing).

        Unlike the root/symbols loaders, a missing artifact is NOT an
        error — Feature B degrades gracefully when the builder didn't
        generate it.
        """
        if self._tests is None:
            path = _index_read(self.project_root, self.TESTS_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._tests = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._tests = {}
        return self._tests

    def _load_routes(self) -> Dict[str, Dict[str, Any]]:
        """Return ``agent_routes.json`` content (or ``{}`` if missing)."""
        if self._routes is None:
            path = _index_read(self.project_root, self.ROUTES_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._routes = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._routes = {}
        return self._routes

    def _load_ng_routes(self) -> List[Dict[str, Any]]:
        """Return ``agent_ng_routes.json`` content (or ``[]`` if missing)."""
        if self._ng_routes is None:
            path = _index_read(self.project_root, self.NG_ROUTES_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                self._ng_routes = data if isinstance(data, list) else []
            except (OSError, json.JSONDecodeError):
                self._ng_routes = []
        return self._ng_routes

    def _load_callbacks(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return ``agent_callbacks.json`` content (or ``{}`` if missing).

        Same graceful-degradation contract as the other optional
        artifact loaders — Feature D is fresh and may be absent on
        older builds.
        """
        if self._callbacks is None:
            path = _index_read(self.project_root, self.CALLBACKS_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._callbacks = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._callbacks = {}
        return self._callbacks

    def _load_fsm_flows(self) -> Dict[str, Dict[str, Any]]:
        """Return ``agent_fsm_flows.json`` content (or ``{}`` if missing)."""
        if self._fsm_flows is None:
            path = _index_read(self.project_root, self.FSM_FLOW_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._fsm_flows = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._fsm_flows = {}
        return self._fsm_flows

    def _load_impact(self) -> Dict[str, Any]:
        """Return ``agent_impact.json`` content (or an empty graph)."""
        if self._impact is None:
            path = _index_read(self.project_root, self.IMPACT_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                self._impact = data if isinstance(data, dict) else {"symbols": {}}
            except (OSError, json.JSONDecodeError):
                self._impact = {"symbols": {}}
        return self._impact

    def _load_test_categories(self) -> Dict[str, Dict[str, Any]]:
        """Return ``agent_test_categories.json`` (or ``{}`` if missing)."""
        if self._test_categories is None:
            path = _index_read(self.project_root, self.TEST_CATEGORIES_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._test_categories = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._test_categories = {}
        return self._test_categories

    def _load_locale_keys(self) -> Dict[str, Dict[str, Any]]:
        """Return ``agent_locale_keys.json`` (or ``{}`` if missing)."""
        if self._locale_keys is None:
            path = _index_read(self.project_root, self.LOCALES_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._locale_keys = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._locale_keys = {}
        return self._locale_keys

    def _load_docs_index(self) -> Dict[str, Any]:
        """Return ``agent_docs_index.json`` (or ``{"docs": {}}`` if
        missing). Lazy-loaded on first markdown query."""
        if self._docs_index is None:
            path = _index_read(self.project_root, self.DOCS_INDEX_FILENAME)
            try:
                with open(path, encoding="utf-8") as fh:
                    self._docs_index = json.load(fh)
            except (OSError, json.JSONDecodeError):
                self._docs_index = {"docs": {}}
        return self._docs_index

    # ─── Markdown docs queries (delegate to markdown_index helpers) ──

    def get_doc_toc(
        self, file: str, *, max_level: Optional[int] = None
    ) -> Optional[List[Dict[str, Any]]]:
        from indexers.markdown_index import get_toc

        return get_toc(self._load_docs_index(), file, max_level=max_level)

    def find_doc_section(
        self,
        file: str,
        header_pattern: Optional[str] = None,
        *,
        fuzzy: bool = True,
        number: Optional[int] = None,
        heading: Optional[str] = None,
        anchor: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        from indexers.markdown_index import find_section

        return find_section(
            self._load_docs_index(),
            file,
            header_pattern,
            fuzzy=fuzzy,
            number=number,
            heading=heading,
            anchor=anchor,
        )

    def list_docs(self, *, path_prefix: Optional[str] = None) -> List[Dict[str, Any]]:
        from indexers.markdown_index import list_docs as _list

        return _list(self._load_docs_index(), path_prefix=path_prefix)

    def search_doc_text(
        self,
        query: str,
        *,
        file: Optional[str] = None,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        # Semantic search when the doc_sections table is built and no regex.
        if not regex:
            try:
                from stores.semantic_store import provider_from_conventions, search_doc_sections

                provider = provider_from_conventions(self.project_root)
                results = search_doc_sections(
                    self.project_root,
                    query,
                    top_k=max_results,
                    file_filter=file,
                    provider=provider,
                )
                if results is not None:
                    return results
            except Exception:
                pass

        from indexers.markdown_index import search_doc_text as _search

        return _search(
            self.project_root,
            self._load_docs_index(),
            query,
            file=file,
            regex=regex,
            case_sensitive=case_sensitive,
            max_results=max_results,
        )

    def find_doc_xref(
        self,
        term: str,
        *,
        case_sensitive: bool = False,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        from indexers.markdown_index import find_xref

        return find_xref(
            self.project_root,
            self._load_docs_index(),
            term,
            case_sensitive=case_sensitive,
            max_results=max_results,
        )

    def docs_link_graph(self) -> Dict[str, Any]:
        from indexers.markdown_index import link_graph

        return link_graph(self.project_root, self._load_docs_index())

    def _iter_module_maps(self) -> Iterable[Tuple[str, Dict[str, Any]]]:
        """Yield ``(relative_directory, parsed_map_json)`` for each
        module map under the project root.
        """
        if self._module_maps is None:
            collected: List[Tuple[str, Dict[str, Any]]] = []
            for cur, dirs, files in os.walk(self.project_root):
                dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]
                if self.MAP_FILENAME not in files:
                    continue
                map_path = os.path.join(cur, self.MAP_FILENAME)
                try:
                    with open(map_path, encoding="utf-8") as fh:
                        data = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    # Best-effort: a corrupt cache is not a query-time
                    # error, the next builder run will rewrite it.
                    continue
                rel_dir = self._rel(cur)
                collected.append((rel_dir, data))
            # Stable ordering for deterministic output.
            collected.sort(key=lambda item: item[0])
            self._module_maps = collected
        return self._module_maps

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rel(self, path: str) -> str:
        """Project-relative path with forward slashes, no leading './'."""
        try:
            rel = os.path.relpath(path, self.project_root)
        except ValueError:
            rel = path
        rel = rel.replace(os.sep, "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if rel == ".":
            return ""
        return rel

    @staticmethod
    def _first_line(doc: Optional[str]) -> Optional[str]:
        if not doc:
            return None
        for line in doc.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return None

    # ------------------------------------------------------------------
    # Public API — one method per RPC
    # ------------------------------------------------------------------

    # Defaults for read_slice — small enough to keep responses in the
    # token-economy band, large enough to fit a typical method body.
    SLICE_MAX_LINES = 200
    SLICE_MAX_BYTES = 8000

    def read_slice(
        self,
        file_path: str,
        start: int,
        end: int,
    ) -> Optional[Dict[str, Any]]:
        """Return ``{file, start, end, content, truncated}`` for a
        small range of a file relative to ``project_root``.

        ``start`` / ``end`` are 1-indexed inclusive line numbers.  The
        return value's ``content`` is a ``\\n``-joined slice (no line
        prefixes — the caller already knows the offsets).  Caps at
        ``SLICE_MAX_LINES`` lines or ``SLICE_MAX_BYTES`` bytes; sets
        ``truncated`` when either limit fired.

        Returns ``None`` when the path escapes ``project_root`` or
        when the file doesn't exist.  This is the consistent
        MCP-channel for source reads — paired with ``find_symbol``'s
        ``line`` / ``end_line`` it lets an agent jump straight to
        evidence without shelling out.
        """
        if not isinstance(start, int) or not isinstance(end, int):
            return None
        if start < 1 or end < start:
            return None
        # Resolve & containment-check.  ``os.path.commonpath`` is the
        # cleanest way to confirm the resolved abspath sits under root
        # without falling for ``..`` chains or symlink trickery.
        rel = file_path.replace(os.sep, "/").lstrip("/")
        abs_path = os.path.abspath(os.path.join(self.project_root, rel))
        try:
            common = os.path.commonpath([abs_path, os.path.abspath(self.project_root)])
        except ValueError:
            return None
        if common != os.path.abspath(self.project_root):
            return None
        if not os.path.isfile(abs_path):
            return None

        cap_end = min(end, start + self.SLICE_MAX_LINES - 1)
        truncated = cap_end < end
        try:
            mtime = os.path.getmtime(abs_path)
            cached = self._file_cache.get(abs_path)
            if cached is not None and cached[0] == mtime:
                all_lines = cached[1]
            else:
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    all_lines = [raw.rstrip("\n") for raw in fh]
                # Evict oldest half when cache is full.
                if len(self._file_cache) >= self._FILE_CACHE_MAX:
                    evict = list(self._file_cache)[: self._FILE_CACHE_MAX // 2]
                    for k in evict:
                        del self._file_cache[k]
                self._file_cache[abs_path] = (mtime, all_lines)
            collected = all_lines[start - 1 : cap_end]
        except OSError:
            return None

        content = "\n".join(collected)
        if len(content.encode("utf-8")) > self.SLICE_MAX_BYTES:
            content = content.encode("utf-8")[: self.SLICE_MAX_BYTES].decode(
                "utf-8", errors="ignore"
            )
            truncated = True

        return {
            "file": rel,
            "start": start,
            "end": cap_end,
            "content": content,
            "truncated": truncated,
        }

    def summarise_module(self, folder: str) -> Optional[Dict[str, Any]]:
        """Return a tight summary of a folder's ``_module_map.json``.

        For each file in the folder we keep the export ``name``, ``kind``,
        ``role``, and the first line of ``doc``. ``params`` are stripped
        to keep the payload small — call ``find_symbol`` if you need a
        signature.

        When a ``chat_provider`` is configured in ``.vc-context/conventions.json``
        the result also includes a ``"summary"`` key with a 2-3 sentence
        natural-language description generated by the LLM. The summary is
        cached per-session by prompt hash, so repeated calls are instant.

        Returns ``None`` when the folder has no module map.
        """
        normalised = folder.replace("\\", "/").strip("/")
        if os.path.isabs(normalised):
            normalised = self._rel(normalised)
        target = os.path.join(self.project_root, normalised) if normalised else self.project_root
        map_path = os.path.join(target, self.MAP_FILENAME)
        if not os.path.exists(map_path):
            return None
        try:
            with open(map_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

        slim_files: Dict[str, Any] = {}
        for fname, fdata in (data.get("files") or {}).items():
            if not isinstance(fdata, dict):
                slim_files[fname] = fdata
                continue
            slim_exports = []
            for exp in fdata.get("exports") or []:
                if not isinstance(exp, dict):
                    slim_exports.append(exp)
                    continue
                slim = {"name": exp.get("name"), "kind": exp.get("kind")}
                role = exp.get("role")
                if role:
                    slim["role"] = role
                first = self._first_line(exp.get("doc"))
                if first:
                    slim["doc"] = first
                slim_exports.append(slim)
            slim_files[fname] = {
                "exports": slim_exports,
                "dependencies": fdata.get("dependencies") or [],
            }

        result: Dict[str, Any] = {
            "directory": data.get("directory") or normalised or ".",
            "files": slim_files,
        }

        # LLM summary — optional, never blocks return on failure.
        try:
            from ollama_chat import chat_provider_from_conventions  # type: ignore[import-not-found]

            chat = chat_provider_from_conventions(self.project_root)
            if chat is not None:
                prompt = self._module_summary_prompt(result)
                import hashlib

                key = hashlib.sha256(prompt.encode()).hexdigest()[:16]
                if key not in self._summary_cache:
                    self._summary_cache[key] = chat.generate(
                        prompt,
                        system=(
                            "You are a code analyst. Given a module's exported symbols "
                            "and their roles, write 2-3 sentences describing what this "
                            "module does and its main responsibilities. Be specific and "
                            "technical. Output only the description, no headers or lists."
                        ),
                        timeout=30,
                    )
                result["summary"] = self._summary_cache[key]
        except Exception:
            pass  # LLM summary is always optional

        return result

    def _module_summary_prompt(self, module_data: Dict[str, Any]) -> str:
        """Build a compact prompt from module data for LLM summarisation."""
        directory = module_data.get("directory", "")
        lines = [f"Module: {directory}"]
        all_deps: set = set()
        for fname, fdata in list((module_data.get("files") or {}).items())[:6]:
            exports = (fdata.get("exports") or []) if isinstance(fdata, dict) else []
            if not exports:
                continue
            lines.append(f"File: {fname}")
            for exp in exports[:8]:
                if not isinstance(exp, dict):
                    continue
                parts = [exp.get("name", "")]
                role = exp.get("role") or exp.get("kind")
                if role:
                    parts.append(f"[{role}]")
                doc = exp.get("doc", "")
                if doc:
                    parts.append(f"— {doc[:80]}")
                lines.append("  " + " ".join(p for p in parts if p))
            deps = (fdata.get("dependencies") or []) if isinstance(fdata, dict) else []
            all_deps.update(deps[:4])
        if all_deps:
            lines.append("Dependencies: " + ", ".join(sorted(all_deps)[:6]))
        return "\n".join(lines)

    def repo_map(self) -> Dict[str, Any]:
        """Return a top-level project shape — module list with file
        counts, export counts, and dominant role per module.

        Built by walking every ``_module_map.json`` once.  The output
        is the cheapest way to ask "what does this project look like?"
        without iterating modules manually.

        Shape::

            {
              "modules": [
                {
                  "path": "./bot/handlers",
                  "files": 12,
                  "exports": 89,
                  "dominant_role": "aiogram-handler",
                  "roles": {"aiogram-handler": 67, "fsm-state": 4}
                },
                ...
              ],
              "totals": {"modules": 14, "files": 83, "exports": 412}
            }
        """
        modules: List[Dict[str, Any]] = []
        total_files = 0
        total_exports = 0
        for mp, data in self._iter_module_maps():
            files = data.get("files") or {}
            file_count = len(files)
            exports = 0
            roles: Dict[str, int] = {}
            for fdata in files.values():
                if not isinstance(fdata, dict):
                    continue
                for exp in fdata.get("exports") or []:
                    if not isinstance(exp, dict):
                        continue
                    exports += 1
                    r = exp.get("role")
                    if r:
                        roles[r] = roles.get(r, 0) + 1
            # `roles.get` confuses mypy's overloaded `max`; lambda
            # makes the key signature unambiguous.
            dominant = max(roles, key=lambda k: roles[k]) if roles else None
            modules.append(
                {
                    "path": data.get("directory") or os.path.dirname(self._rel(mp)),
                    "files": file_count,
                    "exports": exports,
                    "dominant_role": dominant,
                    "roles": roles,
                }
            )
            total_files += file_count
            total_exports += exports
        modules.sort(key=lambda m: m["path"])
        return {
            "modules": modules,
            "totals": {
                "modules": len(modules),
                "files": total_files,
                "exports": total_exports,
            },
        }

    def impact(
        self, symbol: str, *, depth: int = 2, include_tests: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Return symbols/tests likely affected by changing ``symbol``.

        The result is read from ``agent_impact.json`` and bounded by
        ``depth`` (1..5) so callers can ask the refactor question in one
        round-trip without expanding the whole project graph.

        Pass ``include_tests=True`` to merge the ``find_test`` result for
        ``symbol`` into the response as ``result["test"]`` — saves a
        separate ``find_test`` call.
        """
        if not symbol:
            return None
        from indexers.impact_graph import query_impact

        result = query_impact(self._load_impact(), symbol, depth=depth)
        if include_tests and result is not None:
            result["test"] = self.find_test(symbol)
        return result

    def get_file_card(self, path: str) -> Optional[Dict[str, Any]]:
        """Return a single file's summary — exports, dependencies,
        dominant role.  Slim version of ``summarise_module`` scoped to
        one file.

        Pulls from the file's containing folder ``_module_map.json``.
        Each export shows ``name``, ``kind``, ``role``, ``line``,
        ``end_line`` (when indexed) plus the first line of its doc.
        Returns ``None`` when the file isn't in any module map.

        Use to answer "what does this file do?" without reading it.
        """
        rel = path.replace("\\", "/").strip("/")
        if os.path.isabs(rel):
            rel = self._rel(rel)
        if "/" in rel:
            folder, fname = rel.rsplit("/", 1)
        else:
            folder, fname = "", rel
        map_path = os.path.join(self.project_root, folder, self.MAP_FILENAME)
        if not os.path.exists(map_path):
            return None
        try:
            with open(map_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        files = data.get("files") or {}
        fdata = files.get(fname)
        if not isinstance(fdata, dict):
            return None

        slim_exports: List[Dict[str, Any]] = []
        roles_seen: Dict[str, int] = {}
        for exp in fdata.get("exports") or []:
            if not isinstance(exp, dict):
                continue
            slim: Dict[str, Any] = {"name": exp.get("name"), "kind": exp.get("kind")}
            for k in ("role", "line", "end_line"):
                v = exp.get(k)
                if v:
                    slim[k] = v
            first = self._first_line(exp.get("doc"))
            if first:
                slim["doc"] = first
            slim_exports.append(slim)
            r = exp.get("role")
            if r:
                roles_seen[r] = roles_seen.get(r, 0) + 1

        return {
            "file": rel,
            "exports": slim_exports,
            "dependencies": fdata.get("dependencies") or [],
            "roles": roles_seen,
        }

    def list_roles(self) -> Dict[str, int]:
        """``role → count`` map across the whole project.

        Synthetic umbrella counts (e.g. ``aiogram-handler``) are added
        on top of the raw subrole counts so an agent grep'ing for
        "how many aiogram handlers" still finds the answer with one
        lookup.
        """
        root = self._load_root()
        roles = root.get("roles") or {}
        out: Dict[str, int] = {r: len(names) for r, names in roles.items()}
        for umbrella, members in self._ROLE_UMBRELLAS.items():
            seen: set = set()
            for m in members:
                for n in roles.get(m) or ():
                    seen.add(n)
            if seen:
                # Always overwrite with the synthetic count — an existing
                # raw bucket under the umbrella name (legacy fallback
                # tags) is a strict subset of the union, so the synthetic
                # count is the right answer.
                out[umbrella] = len(seen)
        return out

    def list_modules(self) -> List[str]:
        """All scanned module folders, in the order recorded by the builder."""
        root = self._load_root()
        return list(root.get("modules") or [])

    # ------------------------------------------------------------------
    # Feature A — convention linter
    # ------------------------------------------------------------------

    def lint_violations(self) -> List[Dict[str, Any]]:
        """Run the convention linter (``.vc-context/conventions.json``).

        Empty list when the config file is missing — the linter is
        opt-in. See ``conventions.py`` for the rule schema and the
        supported rule kinds.
        """
        # Local import keeps the engine import-cheap when the linter
        # isn't used.
        from conventions import lint_project  # type: ignore[import-not-found]

        return lint_project(self.project_root)

    # find_test / coverage_stats / coverage_for_role / classify_tests /
    # tests_by_category — now provided by ``_TestsMixin``.

    # find_route / route_callers / route_for_js_call / ng_list_routes /
    # ng_route_for_path / ng_routes_for_component / find_callback /
    # trace_fsm_flow — provided by ``_RoutesMixin``.
    # classify_tests / tests_by_category — provided by ``_TestsMixin``.

    def logline_to_symbol(self, line: str) -> Dict[str, Any]:
        """Parse a Python ``logging`` line and resolve to a
        ``{level, logger, file, message, symbol?, symbol_file?, role?}``
        record. ``matched=False`` when the line shape isn't recognised.
        """
        from logline_parser import logline_to_symbol as _resolve  # type: ignore[import-not-found]

        # Pass loaded symbols (or empty dict if missing) so the parser
        # can fold in symbol info when the message leads with a known
        # identifier. The loader caches.
        symbols = (
            self._load_symbols()
            if os.path.isfile(_index_read(self.project_root, self.SYMBOLS_FILENAME))
            else {}
        )
        return _resolve(self.project_root, line, symbols=symbols)

    # ------------------------------------------------------------------
    # Git bridge — symbols touched by current changes
    # ------------------------------------------------------------------

    _DIFF_HUNK_RE = re.compile(
        r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@",
    )

    def get_changed_symbols(
        self,
        base: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return symbols whose ``(file, line, end_line)`` overlap any
        hunk of ``git diff <base>..HEAD`` (default base: working tree
        — i.e. ``git diff`` for unstaged + staged).

        Each item: ``{name, file, line, end_line, kind, role?}``.
        Sorted by ``(file, line)`` for deterministic output.

        Use to scope a refactor review or a CI signal: "which symbols
        did this branch actually touch?"  Symbols whose def block is
        outside any hunk (e.g. a single-line comment edit inside a
        function still touches that function) are still returned —
        the function's full range overlaps.

        Returns ``[]`` when not in a git repo OR when the diff is
        empty.  Symbols not in the index (new files yet to be
        re-indexed) are silently dropped — call
        ``python3 .ai-context/agent_map.py`` and retry.
        """
        try:
            import subprocess  # local import — keeps import time low

            cmd = ["git", "diff", "--unified=0"]
            if base and base.strip():
                cmd.append(f"{base.strip()}..HEAD")
            proc = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []

        # Parse hunks: file -> [(start, end_inclusive), ...]
        hunks: Dict[str, List[tuple]] = {}
        current_file: Optional[str] = None
        for line in proc.stdout.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:].strip()
                continue
            if line.startswith("+++ "):
                # +++ /dev/null (file deleted) — no symbol overlap to record
                current_file = None
                continue
            if not line.startswith("@@") or current_file is None:
                continue
            m = self._DIFF_HUNK_RE.match(line)
            if not m:
                continue
            start = int(m.group(1))
            length = int(m.group(2) or 1)
            if length == 0:
                continue  # pure deletion in old side, no new lines
            hunks.setdefault(current_file, []).append(
                (start, start + length - 1),
            )

        if not hunks:
            return []

        symbols = self._load_symbols()
        out: List[Dict[str, Any]] = []
        for name, entry in symbols.items():
            file_v = entry.get("file")
            if not file_v or file_v not in hunks:
                continue
            sym_start = entry.get("line")
            sym_end = entry.get("end_line") or sym_start
            if not isinstance(sym_start, int) or not isinstance(sym_end, int):
                continue
            for h_start, h_end in hunks[file_v]:
                if h_end < sym_start or h_start > sym_end:
                    continue
                rec: Dict[str, Any] = {
                    "name": name,
                    "file": file_v,
                    "line": sym_start,
                    "end_line": sym_end,
                    "kind": entry.get("kind"),
                }
                role = entry.get("role")
                if role:
                    rec["role"] = role
                out.append(rec)
                break
        out.sort(key=lambda r: (r["file"], r["line"]))
        return out

    # ------------------------------------------------------------------
    # Feature J — whitelisted check runner (tests / lint / typecheck)
    # ------------------------------------------------------------------

    def list_checks(self) -> List[str]:
        """Available check names declared in
        ``.vc-context/conventions.json`` → ``checks``. Empty when
        the block is missing.
        """
        from checks import list_checks as _list  # type: ignore[import-not-found]

        return _list(self.project_root)

    def run_check(
        self,
        name: str,
        timeout_sec: Optional[int] = None,
        args: Optional[List[str]] = None,
        *,
        nocache: bool = False,
    ) -> Dict[str, Any]:
        """Execute a whitelisted check by name. Returns
        ``{name, command, returncode, duration_ms, stdout_tail,
        stderr_tail, summary, error?, cached?}``. Refuses unknown names
        with returncode -2.

        Results are memoised by ``(name, args, git_state_hash)`` so a repeat
        invocation with no source edits returns in ~ms with
        ``cached: True``. The hash covers committed HEAD + staged +
        unstaged + untracked changes via ``git status --porcelain``.
        Pass ``nocache=True`` to bypass the cache (e.g. when something
        outside git changed — environment vars, external services).
        Caching is skipped when the project isn't a git repo or when
        the previous invocation failed to spawn (returncode -3).
        """
        from checks import run_check as _run  # type: ignore[import-not-found]

        extra_args = tuple(args or [])
        if not nocache:
            state = self._git_state_hash()
            if state is not None:
                cached = self._check_cache.get((name, extra_args, state))
                if cached is not None:
                    return {**cached, "cached": True}

        result = _run(self.project_root, name, timeout_sec=timeout_sec, args=args)

        # Cache only successful spawns — re-running after a spawn
        # failure (returncode -3) should give a fresh try once the env
        # is fixed, not a stale "still broken" hit.
        if not nocache and result.get("returncode") != -3:
            state = self._git_state_hash()
            if state is not None:
                self._check_cache[(name, extra_args, state)] = dict(result)
        return result

    def run_checks(
        self,
        names: List[str],
        timeout_sec: Optional[int] = None,
        *,
        nocache: bool = False,
    ) -> List[Dict[str, Any]]:
        """Run multiple whitelisted checks in parallel.

        Returns results in the same order as ``names``, each identical in
        shape to a single ``run_check`` result. Runs up to 4 checks
        concurrently via ``ThreadPoolExecutor``. Caching, timeout, and
        nocache semantics are the same as for ``run_check``.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not names:
            return []

        results: List[Optional[Dict[str, Any]]] = [None] * len(names)
        with ThreadPoolExecutor(max_workers=min(len(names), 4)) as pool:
            future_to_idx = {
                pool.submit(self.run_check, name, timeout_sec, None, nocache=nocache): i
                for i, name in enumerate(names)
            }
            for future in as_completed(future_to_idx):
                i = future_to_idx[future]
                try:
                    results[i] = future.result()
                except Exception as exc:
                    results[i] = {
                        "name": names[i],
                        "command": [],
                        "returncode": -3,
                        "duration_ms": 0,
                        "stdout_tail": "",
                        "stderr_tail": "",
                        "summary": None,
                        "error": str(exc),
                    }
        return [r for r in results if r is not None]

    def _git_state_hash(self) -> Optional[str]:
        """SHA-256 of ``git rev-parse HEAD`` + ``git status --porcelain``
        — a stable fingerprint of every change visible to git.

        Returns ``None`` when the project isn't a git repo (treated as
        "uncacheable" — no key to safely deduplicate against).
        """
        import hashlib
        import subprocess

        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if head.returncode != 0:
                return None
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if status.returncode != 0:
                return None
        except (OSError, subprocess.TimeoutExpired):
            return None

        h = hashlib.sha256()
        h.update(head.stdout.strip().encode("utf-8"))
        h.update(b"\n")
        h.update(status.stdout.encode("utf-8"))
        return h.hexdigest()

    # ------------------------------------------------------------------
    # Template search
    # ------------------------------------------------------------------

    def find_in_templates(
        self,
        pattern: str,
        match_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search Angular HTML templates for *pattern* (case-insensitive).

        Walks every ``.html`` file under ``project_root``, skipping
        ``IGNORE_DIRS``.  Returns a list of ``{file, line, text}`` dicts,
        one per matching line, capped at ``_MAX_RESULTS`` (50). Each
        ``text`` is right-trimmed and truncated to ``_TEXT_MAX`` (200)
        chars — Angular template lines (long ``tw:`` class strings) would
        otherwise dominate the payload. Mirrors ``find_in_file``'s bounds.

        Parameters
        ----------
        pattern:
            Substring to look for (CSS class, Angular binding expression,
            selector tag, event handler, etc.).  Matched case-insensitively.
        match_path:
            Optional ``fnmatch``-style glob applied to the relative file path,
            e.g. ``"collection-player-v2/**"``.  Only files whose relative
            path matches are searched.
        """
        import fnmatch

        max_results = 50
        text_max = 200

        needle = pattern.lower()
        results: List[Dict[str, Any]] = []

        for dirpath, dirnames, filenames in os.walk(self.project_root):
            # Prune ignored dirs in-place so os.walk skips them entirely.
            dirnames[:] = [d for d in dirnames if d not in self.IGNORE_DIRS]
            for fname in filenames:
                if not fname.endswith(".html"):
                    continue
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, self.project_root).replace("\\", "/")
                if match_path and not fnmatch.fnmatch(rel_path, match_path):
                    continue
                try:
                    with open(abs_path, encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if needle in line.lower():
                                results.append(
                                    {
                                        "file": rel_path,
                                        "line": lineno,
                                        "text": line.rstrip()[:text_max],
                                    }
                                )
                                if len(results) >= max_results:
                                    results.sort(key=lambda r: (r["file"], r["line"]))
                                    return results
                except OSError:
                    continue

        results.sort(key=lambda r: (r["file"], r["line"]))
        return results

    # ------------------------------------------------------------------
    # Angular helpers (Feature P)
    # ------------------------------------------------------------------

    def ng_audit_component(self, name: str) -> Optional[Dict[str, Any]]:
        """Composite audit for one Angular @Component class.

        Returns a flattened record built from agent_symbols.json plus
        agent_tests.json — selector / templateUrl / standalone / inputs
        / outputs / styleUrls / nearest test. Returns ``None`` when the
        symbol is unknown or not an ng-component.
        """
        sym = self.find_symbol(name)
        if sym is None or sym.get("role") != "ng-component":
            return None
        test = self.find_test(name)
        return {
            "name": name,
            "file": sym.get("file"),
            "role": "ng-component",
            "selector": sym.get("ng_selector"),
            "template_url": sym.get("ng_template_url"),
            "style_urls": sym.get("ng_style_urls", []),
            "standalone": sym.get("ng_standalone"),
            "inputs": sym.get("inputs", []),
            "outputs": sym.get("outputs", []),
            "doc": sym.get("doc"),
            "test": test,
        }

    def ng_uses_selector(
        self,
        selector: str,
        match_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Find HTML templates referencing *selector* as either an
        element (``<selector``) or attribute directive (``[selector]``).

        Two passes through ``find_in_templates`` deduplicated by
        ``(file, line)``. Capped at 100 results.
        """
        if not selector:
            return []
        seen: set = set()
        out: List[Dict[str, Any]] = []
        for needle in (f"<{selector}", f"[{selector}]"):
            for hit in self.find_in_templates(needle, match_path=match_path):
                key = (hit["file"], hit["line"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(hit)
                if len(out) >= 100:
                    return out
        return out

    def ng_overview(self) -> Dict[str, Any]:
        """Zero-arg snapshot of the Angular surface area.

        Returns role counts plus a list of services with
        ``providedIn: 'root'`` and a count of standalone components.
        Cheap — single pass over agent_symbols.json.
        """
        symbols = self._load_symbols()
        roles = (
            "ng-component",
            "ng-service",
            "ng-module",
            "ng-pipe",
            "ng-directive",
            "ng-guard",
        )
        counts: Dict[str, int] = {role: 0 for role in roles}
        standalone = 0
        providers_root: List[str] = []
        for name, rec in symbols.items():
            role = rec.get("role")
            if role in counts:
                counts[role] += 1
            if role == "ng-component" and rec.get("ng_standalone") is True:
                standalone += 1
            if role == "ng-service" and rec.get("ng_provided_in") == "root":
                providers_root.append(name)
        providers_root.sort()
        return {
            "counts": counts,
            "standalone_components": standalone,
            "providers_root": providers_root,
        }

    def ng_inject_graph(self, service: str) -> List[Dict[str, Any]]:
        """Heuristic DI injection lookup. Two modes:

        **Service mode** (default) — pass a service class name:
        Returns every file where ``ServiceName`` is injected:

        * ``constructor(... : ServiceName)`` — classic DI.
        * ``inject(ServiceName)`` — Angular 14+ functional inject.

        Result: ``[{file, line, kind, service}]``
        where kind ∈ ``constructor`` / ``inject``.

        **Module mode** — pass an NgModule class name:
        Returns ALL injection points within that module's source
        files, grouped by component. Each record also carries a
        ``service`` field with the injected type name.

        Result: ``[{file, line, kind, service}]`` for every
        ``inject(X)`` / ``: X`` found in the module's tree.

        Auto-detects which mode to use: if ``service`` is found in
        ``ng_module_members``, module mode is used; otherwise falls
        back to service mode (existing behaviour).
        """
        if not service:
            return []
        import re as _re

        # ── Module mode ──────────────────────────────────────────────
        module_info = self.ng_module_members(service)  # type: ignore[attr-defined]
        if module_info:
            return self._ng_inject_graph_for_module(module_info, _re)

        # ── Service mode (original behaviour) ────────────────────────
        ctor_re = _re.compile(r":\s*" + _re.escape(service) + r"\b")
        inject_re = _re.compile(r"\binject\s*\(\s*" + _re.escape(service) + r"\s*[,\)]")

        out: List[Dict[str, Any]] = []
        for folder, mmap in self._iter_module_maps():
            for filename in mmap.get("files", {}):
                if not filename.endswith((".ts", ".tsx")):
                    continue
                rel = os.path.join(folder, filename).replace("\\", "/")
                abs_path = os.path.join(self.project_root, rel)
                try:
                    with open(abs_path, encoding="utf-8", errors="replace") as fh:
                        for lineno, raw in enumerate(fh, 1):
                            if ctor_re.search(raw):
                                out.append(
                                    {
                                        "file": rel,
                                        "line": lineno,
                                        "kind": "constructor",
                                        "service": service,
                                    }
                                )
                            elif inject_re.search(raw):
                                out.append(
                                    {
                                        "file": rel,
                                        "line": lineno,
                                        "kind": "inject",
                                        "service": service,
                                    }
                                )
                except OSError:
                    continue
                if len(out) >= 200:
                    return out
        return out

    def _ng_inject_graph_for_module(
        self,
        module_info: Dict[str, Any],
        _re: Any,
    ) -> List[Dict[str, Any]]:
        """Collect all DI injection points in a module's source tree."""
        module_file: str = module_info.get("file", "")
        if not module_file:
            return []

        # Derive the module folder from the module file path.
        module_dir = os.path.dirname(module_file).replace("\\", "/")

        # Regex: captures the injected type from both patterns.
        # constructor(private x: TypeName,  or  inject(TypeName)
        ctor_any_re = _re.compile(r"(?:private|public|protected|readonly)\s+\w+\s*:\s*([A-Z]\w+)")
        inject_any_re = _re.compile(r"\binject\s*\(\s*([A-Z]\w+)\s*[,\)]")

        out: List[Dict[str, Any]] = []
        abs_dir = os.path.join(self.project_root, module_dir)

        for dirpath, dirs, files in os.walk(abs_dir):
            dirs[:] = [
                d for d in dirs if d not in {"node_modules", "__pycache__", ".git", "dist", "build"}
            ]
            for fname in files:
                if not fname.endswith((".ts", ".tsx")):
                    continue
                if ".spec." in fname or ".test." in fname:
                    continue
                abs_path = os.path.join(dirpath, fname)
                rel = os.path.relpath(abs_path, self.project_root).replace("\\", "/")
                try:
                    with open(abs_path, encoding="utf-8", errors="replace") as fh:
                        for lineno, raw in enumerate(fh, 1):
                            m = ctor_any_re.search(raw)
                            if m:
                                out.append(
                                    {
                                        "file": rel,
                                        "line": lineno,
                                        "kind": "constructor",
                                        "service": m.group(1),
                                    }
                                )
                                continue
                            m = inject_any_re.search(raw)
                            if m:
                                out.append(
                                    {
                                        "file": rel,
                                        "line": lineno,
                                        "kind": "inject",
                                        "service": m.group(1),
                                    }
                                )
                except OSError:
                    continue
                if len(out) >= 500:
                    return out
        return out

    # Per-instance ESLint cache: key → (timestamp, results)
    # Avoids re-running the 40+ s subprocess for the same path twice in
    # one MCP session. TTL=300s — stale enough to avoid thrashing, fresh
    # enough that a re-lint within the same session picks up edits.
    _eslint_cache: ClassVar[Dict[str, Any]] = {}
    _summary_cache: ClassVar[Dict[str, str]] = {}  # sha256(prompt) → LLM text
    # keyed by (rule_name, abs_path, mtime_int) → hits
    _llm_antipattern_cache: ClassVar[Dict[Tuple[Any, ...], List[Dict[str, Any]]]] = {}
    _ESLINT_CACHE_TTL = 300

    def ng_eslint_violations(
        self,
        path: Optional[str] = None,
        max_results: int = 100,
    ) -> List[Dict[str, Any]]:
        """Run ESLint on a path and return structured violations.

        ``path`` is project-relative (default: ``src``). Returns
        ``[{file, line, col, severity, rule, message}]`` capped at
        ``max_results``. Uses ``npx eslint --format json`` so the
        project's own eslint config and plugins are always respected.
        Returns ``[{error}]`` on spawn failure.

        Results are cached for ``_ESLINT_CACHE_TTL`` seconds per path
        to avoid re-running the 40+ s subprocess multiple times in one
        session.
        """
        import json as _json
        import subprocess as _sp
        import time as _time

        target_rel = path or "src"
        cache_key = f"{self.project_root}:{target_rel}"
        cached = self._eslint_cache.get(cache_key)
        now = _time.monotonic()
        if cached and (now - cached[0]) < self._ESLINT_CACHE_TTL:
            raw_results: List[Dict[str, Any]] = cached[1]
            return raw_results[:max_results]

        abs_target = os.path.join(self.project_root, target_rel)
        try:
            proc = _sp.run(
                ["npx", "eslint", "--format", "json", abs_target],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            raw = (proc.stdout or "").strip()
            if not raw:
                self._eslint_cache[cache_key] = (now, [])
                return []
            data = _json.loads(raw)
        except _sp.TimeoutExpired:
            return [{"error": "eslint timed out after 120s"}]
        except Exception as exc:
            return [{"error": str(exc)}]

        out: List[Dict[str, Any]] = []
        for file_result in data:
            rel = os.path.relpath(file_result.get("filePath", ""), self.project_root).replace(
                "\\", "/"
            )
            for msg in file_result.get("messages", []):
                out.append(
                    {
                        "file": rel,
                        "line": msg.get("line"),
                        "col": msg.get("column"),
                        "severity": "error" if msg.get("severity") == 2 else "warn",
                        "rule": msg.get("ruleId"),
                        "message": msg.get("message"),
                    }
                )
        self._eslint_cache[cache_key] = (now, out)
        return out[:max_results]

    def ng_find_module(self, component_name: str) -> Optional[Dict[str, Any]]:
        """Find the NgModule that declares a given Angular symbol.

        Scans every ``ng-module`` file in the symbol index for a word
        boundary match of ``component_name``. Returns
        ``{module, file}`` for the first match, or ``null`` when not
        found. Confirm by reading the file — this is a substring scan,
        not a full TS resolver.
        """
        if not component_name:
            return None
        import re as _re

        pattern = _re.compile(r"\b" + _re.escape(component_name) + r"\b")
        symbols = self._load_symbols()
        modules = [
            (sym_name, s)
            for sym_name, s in symbols.items()
            if isinstance(s, dict) and s.get("role") == "ng-module"
        ]
        for sym_name, mod in modules:
            file = mod.get("file")
            if not file:
                continue
            abs_path = os.path.join(self.project_root, file)
            try:
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                if pattern.search(content):
                    return {"module": sym_name, "file": file}
            except OSError:
                continue
        return None

    def ng_ts_class_shape(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the public shape of a TypeScript class.

        Reads the class source and extracts (via regex):
        - ``constructor_params`` — name + type for each DI param
        - ``inputs`` — ``@Input()`` property names
        - ``outputs`` — ``@Output()`` property names
        - ``public_methods`` — non-private method names

        Use before refactoring a component or service — replaces the
        Python-only ``inspect_class`` for Angular/TypeScript code.
        Returns ``null`` when the symbol is not indexed.
        """
        import re as _re

        sym = self._symbols_get(name)
        if not sym:
            return None
        file = sym.get("file")
        if not file:
            return None
        abs_path = os.path.join(self.project_root, file)
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            return None

        # Constructor params — handles multi-line constructors
        params: List[Dict[str, Any]] = []
        ctor_m = _re.search(r"constructor\s*\(([^)]*)\)", content, _re.DOTALL)
        if ctor_m:
            raw_params = ctor_m.group(1).strip()
            for tok in _re.split(r",(?![^<>]*>)", raw_params):
                tok = tok.strip()
                if not tok:
                    continue
                # strip access modifiers and decorators
                tok_clean = _re.sub(r"@\w+\([^)]*\)\s*", "", tok)
                tok_clean = _re.sub(
                    r"\b(private|public|protected|readonly)\s+", "", tok_clean
                ).strip()
                parts = tok_clean.split(":", 1)
                pname = parts[0].strip()
                ptype = parts[1].strip() if len(parts) > 1 else None
                if pname:
                    params.append({"name": pname, "type": ptype})

        inputs = _re.findall(r"@Input\(\)\s+(?:readonly\s+)?(\w+)", content)
        outputs = _re.findall(r"@Output\(\)\s+(?:readonly\s+)?(\w+)", content)

        # Public methods — skip private/protected/lifecycle hooks and JS keywords
        _skip = {
            "constructor",
            "ngOnInit",
            "ngOnDestroy",
            "ngOnChanges",
            "ngAfterViewInit",
            "ngAfterContentInit",
            "ngAfterViewChecked",
            "ngAfterContentChecked",
            "ngDoCheck",
            "if",
            "for",
            "while",
            "switch",
            "catch",
            "function",
        }
        method_re = _re.compile(
            r"(?:^|\n)\s*(?:(?:public|async)\s+)*"
            r"(?!private\b|protected\b|get\s|set\s)(\w+)\s*\([^)]*\)\s*(?::\s*\S+\s*)?\{",
            _re.MULTILINE,
        )
        methods = [
            m for m in method_re.findall(content) if m not in _skip and not m.startswith("_")
        ]

        return {
            "name": name,
            "file": file,
            "constructor_params": params,
            "inputs": inputs,
            "outputs": outputs,
            "public_methods": list(dict.fromkeys(methods)),
        }

    def ng_ajs_find(self, name: str) -> Optional[Dict[str, Any]]:
        """Find an AngularJS symbol definition.

        Searches for ``.component(name``, ``.service(name``,
        ``.directive(name``, ``.filter(name``, ``.factory(name``, and
        ``.controller(name`` patterns in two locations:

        * ``app/`` — all ``*.ts`` / ``*.js`` files (legacy AJS tree).
        * ``src/`` — only ``*.ajs.ts`` / ``*.ajs.js`` files (Angular/AJS
          bridge files such as downgraded services registered via
          ``downgradeInjectable``).

        Returns ``{name, kind, file, line}`` for the first match, or
        ``null``.  Fills the gap left by ``find_symbol`` which only
        indexes Angular (``src/``) TypeScript classes.
        """
        if not name:
            return None
        import re as _re

        kinds = ["component", "service", "directive", "filter", "factory", "controller"]
        _kinds_pat = "|".join(kinds)

        def _make_pattern(sym: str, flags: int = 0) -> _re.Pattern[str]:
            return _re.compile(
                r"\.\s*(" + _kinds_pat + r")\s*\(\s*['\"]" + _re.escape(sym) + r"['\"]",
                flags,
            )

        # Collect all candidate files once so we can reuse for fallback passes.
        def _candidate_files() -> List[str]:
            paths: List[str] = []
            app_root = os.path.join(self.project_root, "app")
            if os.path.isdir(app_root):
                for dp, dirs, files in os.walk(app_root):
                    dirs[:] = [d for d in dirs if d not in {"node_modules", "__pycache__"}]
                    for fn in files:
                        if fn.endswith((".ts", ".js")):
                            paths.append(os.path.join(dp, fn))
            src_root = os.path.join(self.project_root, "src")
            if os.path.isdir(src_root):
                for dp, dirs, files in os.walk(src_root):
                    dirs[:] = [d for d in dirs if d not in {"node_modules", "__pycache__"}]
                    for fn in files:
                        if fn.endswith(".ajs.ts") or fn.endswith(".ajs.js"):
                            paths.append(os.path.join(dp, fn))
            return paths

        # canonical_pat extracts the actual registered name from the match line.
        _canonical_pat = _re.compile(r"\.\s*(?:" + _kinds_pat + r")\s*\(\s*['\"](\w+)['\"]")

        def _scan_one(abs_path: str, pat: _re.Pattern[str], sym: str) -> Optional[Dict[str, Any]]:
            rel = os.path.relpath(abs_path, self.project_root).replace("\\", "/")
            try:
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        m = pat.search(line)
                        if m:
                            # Use canonical name from the file (handles case-insensitive hits).
                            cm = _canonical_pat.search(line)
                            canonical = cm.group(1) if cm else sym
                            result = {
                                "name": canonical,
                                "kind": m.group(1),
                                "file": rel,
                                "line": lineno,
                            }
                            if canonical != sym:
                                result["queried_as"] = sym
                            return result
            except OSError:
                pass
            return None

        # Collect files once for all passes.
        files = _candidate_files()

        # Pass 1: exact case-sensitive match.
        exact_pat = _make_pattern(name)
        for fpath in files:
            hit = _scan_one(fpath, exact_pat, name)
            if hit:
                return hit

        # Pass 2: case-insensitive fallback — handles common mistake of passing
        # "learningObjectRegistration" when the AJS token is "LearningObjectRegistration".
        ci_pat = _make_pattern(name, _re.IGNORECASE)
        for fpath in files:
            hit = _scan_one(fpath, ci_pat, name)
            if hit:
                # Use the canonical name from the file, not the query.
                return hit

        # Pass 3: nothing found — collect all AJS registrations and suggest
        # the closest ones so the agent can correct the query.
        all_reg_pat = _re.compile(r"\.\s*(" + _kinds_pat + r")\s*\(\s*['\"](\w+)['\"]")
        all_names: List[Dict[str, Any]] = []
        for fpath in files:
            rel = os.path.relpath(fpath, self.project_root).replace("\\", "/")
            try:
                with open(fpath, encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        m = all_reg_pat.search(line)
                        if m:
                            all_names.append(
                                {
                                    "name": m.group(2),
                                    "kind": m.group(1),
                                    "file": rel,
                                    "line": lineno,
                                }
                            )
            except OSError:
                pass

        # Score by substring containment (both directions) then by shared prefix length.
        query_lower = name.lower()

        def _score(reg_name: str) -> int:
            rn = reg_name.lower()
            if query_lower == rn:
                return 100
            if query_lower in rn or rn in query_lower:
                return 50
            # shared prefix length
            prefix = 0
            for a, b in zip(query_lower, rn):
                if a == b:
                    prefix += 1
                else:
                    break
            return prefix

        candidates = sorted(all_names, key=lambda r: -_score(r["name"]))
        top = [r for r in candidates if _score(r["name"]) > 0][:5]

        if top:
            return {
                "name": name,
                "found": False,
                "note": (
                    f"No AJS registration found for '{name}'. "
                    "It may not exist in the legacy app/ tree, or it is an Angular-only symbol. "
                    "Closest registrations:"
                ),
                "suggestions": top,
            }

        return None

    def ng_module_members(self, module_name: str) -> Optional[Dict[str, Any]]:
        """Return the declarations, imports, exports, and providers of an NgModule.

        Finds the module file via the symbol index, then extracts each
        array from the ``@NgModule({...})`` decorator using a bracket-
        balanced scan. Returns ``{module, file, declarations, imports,
        exports, providers}`` with each array as a list of identifier
        strings. Returns ``null`` when the module is not found.
        """
        if not module_name:
            return None
        import re as _re

        symbols = self._load_symbols()
        mod = symbols.get(module_name)
        if not mod or mod.get("role") != "ng-module":
            # fallback: search by suffix
            for sym_name, sym in symbols.items():
                if (
                    isinstance(sym, dict)
                    and sym.get("role") == "ng-module"
                    and sym_name.lower() == module_name.lower()
                ):
                    mod = sym
                    break
        if not mod:
            return None

        file = mod.get("file") if isinstance(mod, dict) else None
        if not file:
            return None
        abs_path = os.path.join(self.project_root, file)
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            return None

        def _extract_array(key: str, src: str) -> List[str]:
            """Pull identifiers from `key: [ ... ]` inside @NgModule."""
            pat = _re.compile(key + r"\s*:\s*\[", _re.MULTILINE)
            m = pat.search(src)
            if not m:
                return []
            start = m.end()
            depth = 1
            i = start
            while i < len(src) and depth:
                if src[i] == "[":
                    depth += 1
                elif src[i] == "]":
                    depth -= 1
                i += 1
            block = src[start : i - 1]
            # strip comments and newlines, then grab identifiers
            block = _re.sub(r"//[^\n]*", "", block)
            block = _re.sub(r"/\*.*?\*/", "", block, flags=_re.DOTALL)
            return [t.strip() for t in _re.findall(r"\b([A-Z]\w+)\b", block)]

        return {
            "module": module_name,
            "file": file,
            "declarations": _extract_array("declarations", content),
            "imports": _extract_array("imports", content),
            "exports": _extract_array("exports", content),
            "providers": _extract_array("providers", content),
        }
