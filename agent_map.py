import os
import sys
import json
import logging
from typing import Dict, List, Set

# Ensure sibling modules (`symbols`, `parsers`) resolve when this script is
# invoked from the project root via `python3 .ai-context/agent_map.py`.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Import our custom heuristic parser
from parsers import get_parser, get_supported_extensions, get_supported_filenames
from parsers.python_parser import PythonParser
from symbols import extract_scheduler_jobs_from_codebase
from custom_roles import (
    apply_custom_roles,
    load_custom_roles,
    should_override_builtin,
)

# Feature artifacts — built after the symbol index in `run()`.
from test_linking import build_test_index, write_test_index, TESTS_FILENAME
from route_bridge import build_route_index, write_route_index, ROUTES_FILENAME
from ng_route_bridge import (
    NG_ROUTES_FILENAME,
    build_ng_route_index,
    write_ng_route_index,
)
from callback_index import (
    CALLBACKS_FILENAME,
    collect_callbacks,
    write_callback_index,
)
from fsm_flow import (
    FSM_FLOW_FILENAME,
    collect_fsm_flow,
    write_fsm_flow,
)
from test_classifier import (
    TEST_CATEGORIES_FILENAME,
    collect_test_categories,
    write_test_categories,
)
from locale_index import (
    LOCALES_FILENAME,
    build_locale_index,
    write_locale_index,
)
import parse_cache

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

class ContextBuilder:
    """
    Builds a hierarchical RAG context map for AI agents.
    Generates module-level and root-level maps to optimize token usage.
    """

    def __init__(self, root_dir: str = '.'):
        self.root_dir = root_dir
        self.ignore_dirs = {
            '.git', 'node_modules', 'vendor', '__pycache__',
            'dist', 'dist_webpack', 'build', '.venv', 'venv', '.idea', '.vscode'
        }
        # The core dynamically queries parsers for supported formats
        self.allowed_exts = get_supported_extensions()
        self.allowed_filenames = get_supported_filenames()

        self.map_filename = '_module_map.json'
        self.root_map_filename = 'agent_root.json'
        self.symbols_filename = 'agent_symbols.json'
        self.tests_filename = TESTS_FILENAME
        self.routes_filename = ROUTES_FILENAME
        self.ng_routes_filename = NG_ROUTES_FILENAME
        self.callbacks_filename = CALLBACKS_FILENAME
        self.fsm_flow_filename = FSM_FLOW_FILENAME
        self.test_categories_filename = TEST_CATEGORIES_FILENAME
        self.locales_filename = LOCALES_FILENAME
        self.readme_filename = 'AGENT_README.md'
        self.processed_modules: List[str] = []

        # File-level parse cache (Feature S — incremental builds).
        # Loaded once at start; ``put`` populates it as we parse;
        # ``save`` persists at the end of ``run()``.  When the cache
        # epoch changes (conventions.json / roles.json edited) the
        # loader returns an empty entries dict, forcing a full rebuild.
        self.parse_cache = parse_cache.load(self.root_dir)
        # Track which files were touched this run so we can prune
        # entries for deleted files at save time.
        self._touched_files: Set[str] = set()
        self._cache_hits = 0
        self._cache_misses = 0

        # Top-level project dirs that look like packages — used to prune
        # stdlib + third-party noise from dependency lists.
        self.own_packages = self._discover_own_packages()

        # One-shot AST scan: every callable name registered as a
        # scheduler job, so the parser can tag them later.
        self.scheduler_jobs: Set[str] = extract_scheduler_jobs_from_codebase(
            self.root_dir, self.ignore_dirs
        )
        if self.scheduler_jobs:
            logging.info(
                "Detected %d scheduler-job callable(s).", len(self.scheduler_jobs)
            )

        # Project-declared custom roles via .vc-context/roles.json. Empty
        # list when the file is absent — opt-in, no error path.
        self.custom_roles = load_custom_roles(self.root_dir)
        if self.custom_roles:
            logging.info(
                "Loaded %d custom role(s) from %s.",
                len(self.custom_roles),
                os.path.join(".vc-context", "roles.json"),
            )

    def _discover_own_packages(self) -> set:
        """Top-level dir names (e.g. 'bot', 'services') treated as the
        project's own. A dir qualifies if it has an __init__.py or any
        *.py file at its root. Stdlib + external libs are absent here, so
        the dependency filter drops them automatically."""
        own = set()
        try:
            for entry in os.listdir(self.root_dir):
                if entry in self.ignore_dirs or entry.startswith('.'):
                    continue
                path = os.path.join(self.root_dir, entry)
                if not os.path.isdir(path):
                    continue
                try:
                    contents = os.listdir(path)
                except OSError:
                    continue
                if '__init__.py' in contents or any(c.endswith('.py') for c in contents):
                    own.add(entry)
        except OSError:
            pass
        return own

    def _filter_deps(self, deps):
        """Keep only deps that look like own-project packages."""
        if not isinstance(deps, list):
            return deps
        if not self.own_packages:
            return sorted(deps)
        return sorted({d for d in deps if d in self.own_packages})

    def _scan_directories(self, current_dir: str) -> None:
        for root, dirs, files in os.walk(current_dir):
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs]

            valid_files = []
            for f in files:
                if f in self.allowed_filenames or os.path.splitext(f)[1] in self.allowed_exts:
                    valid_files.append(f)

            if not valid_files:
                continue

            self.processed_modules.append(root)

            if self._needs_update(root, valid_files):
                logging.info(f"Updating context map for: {root}")
                self._build_module_map(root, valid_files)

    def _build_module_map(self, dir_path: str, files: List[str]) -> None:
        map_file_path = os.path.join(dir_path, self.map_filename)
        rendered = {}

        for f in files:
            file_path = os.path.join(dir_path, f)
            rel_path = os.path.relpath(file_path, self.root_dir).replace(os.sep, "/")
            self._touched_files.add(rel_path)
            parser = get_parser(f)
            if parser is None:
                data = {"exports": [], "dependencies": []}
            else:
                # Per-file cache lookup — skip parsing when (mtime, size)
                # match the previous build's record. Custom roles run
                # AFTER this point, so per-file overrides applied via
                # roles.json invalidate the whole cache via epoch (the
                # roles.json mtime contributes to the epoch).
                cached = parse_cache.get(self.parse_cache, rel_path, file_path)
                if cached is not None:
                    self._cache_hits += 1
                    # The parser layer used to stash `_body` /
                    # `_register_call` etc. on each export so
                    # custom_roles can inspect them.  Cached entries
                    # have those fields stripped (we only persist what
                    # ends up on disk), which is fine — the cache hit
                    # path implies the file's role assignment is also
                    # cached, so custom_roles doesn't need to re-run
                    # against it. Build fidelity is preserved because
                    # any conventions/roles edit bumps the cache epoch.
                    data = cached
                else:
                    self._cache_misses += 1
                    if isinstance(parser, PythonParser):
                        data = parser.extract(file_path, scheduler_jobs=self.scheduler_jobs)
                    else:
                        # TS / JS parser supports an optional `project_root`
                        # kwarg for the AST upgrade path; passing it
                        # unconditionally is safe (other parsers ignore it
                        # via their own signature).
                        try:
                            data = parser.extract(file_path, project_root=self.root_dir)
                        except TypeError:
                            data = parser.extract(file_path)

            # Filter dependency noise (stdlib + third-party).
            data["dependencies"] = self._filter_deps(data.get("dependencies", []))

            if cached is None:
                # Apply project-declared custom roles. Has to run AFTER the
                # parser (so the export already has its built-in role for
                # priority comparisons) and BEFORE we strip helper fields
                # like `_body` that custom_roles regexes need.
                self._apply_custom_roles_to_exports(data, file_path)

                # Strip parser-private helper fields (_body, _anchor,
                # _register_call) — they're only needed in-memory by
                # custom_roles, never serialised to disk.
                for exp in data.get("exports", []) or []:
                    if not isinstance(exp, dict):
                        continue
                    for hidden in ("_body", "_anchor", "_register_call",
                                   "_decorators_text"):
                        exp.pop(hidden, None)

                # Cache the finalised payload — same shape that ends up
                # in _module_map.json on disk. Custom roles + private
                # field strip have already run, so the cache hit branch
                # can skip both.
                parse_cache.put(self.parse_cache, rel_path, file_path, data)

            # Skip files that have no public exports AND no own-project deps.
            # That kills empty __init__.py files + glue files an agent doesn't
            # need to know about.
            if not data.get("exports") and not data.get("dependencies"):
                continue

            rendered[f] = data

        if not rendered:
            # Whole directory has nothing interesting — drop the map entirely
            # so it doesn't sit there as a 200-byte stub that just costs tokens.
            if os.path.exists(map_file_path):
                try:
                    os.remove(map_file_path)
                except OSError:
                    pass
            return

        module_data = {"directory": dir_path, "files": rendered}
        try:
            with open(map_file_path, 'w', encoding='utf-8') as f:
                json.dump(module_data, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except IOError as e:
            logging.error(f"Failed to write {map_file_path}: {e}")

    def _apply_custom_roles_to_exports(self, data: Dict, file_path: str) -> None:
        """Walk every export in ``data`` and apply user-declared rules.

        ``data`` is mutated in-place: each export may gain (or have its
        existing) ``role`` overwritten according to priority. Built-in
        roles are kept when no custom rule beats their priority of 0.

        ``file_path`` is the absolute / relative path the parser worked
        on. Custom rules' ``match_path`` glob is evaluated against the
        project-relative form.
        """
        if not self.custom_roles:
            return

        exports = data.get("exports") or []
        if not exports:
            return

        # Read the source ONCE per file — custom_role regexes need it
        # for the body-level matchers when the parser didn't stash a
        # private `_body` field.
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                source_text = fh.read()
        except OSError:
            source_text = ""

        for exp in exports:
            if not isinstance(exp, dict):
                continue
            existing_role = exp.get("role")
            new_role = apply_custom_roles(
                exp,
                file_path,
                source_text,
                self.custom_roles,
                project_root=self.root_dir,
            )
            if not new_role:
                continue
            # Look up the priority of the matched rule.
            new_priority = self._priority_for(new_role)
            if should_override_builtin(new_role, new_priority, existing_role):
                exp["role"] = new_role

    def _priority_for(self, role_id: str) -> int:
        for rule in self.custom_roles:
            if rule.id == role_id:
                return rule.priority
        return 0

    def run(self) -> None:
        """Main entry point to start the scanning process."""
        logging.info("Starting vc-context-builder...")
        self._scan_directories(self.root_dir)
        self._build_root_map()
        # Symbol index uses the maps just written, so it has to come
        # AFTER root-map and BEFORE the SOP regen (so the SOP can refer
        # to it by name without the file going stale).
        self._build_symbol_index()
        # Feature B + C — depend on the symbol index being on disk.
        self._build_test_index()
        self._build_route_index()
        self._build_ng_route_index()
        self._build_callback_index()
        self._build_fsm_flow_index()
        self._build_test_categories_index()
        self._build_locale_index()
        self._generate_agent_sop()
        self._save_parse_cache()
        logging.info("Context build complete. Agent SOP is ready.")

    def _save_parse_cache(self) -> None:
        """Persist the file-level parse cache and report hit ratio.

        Pruning happens here too — entries for files that weren't
        touched this run are dropped, keeping the cache file from
        growing unbounded as the project evolves.
        """
        if self._touched_files:
            parse_cache.prune(self.parse_cache, self._touched_files)
        try:
            parse_cache.save(self.root_dir, self.parse_cache)
        except OSError as exc:
            logging.warning("Failed to write parse cache: %s", exc)
            return
        total = self._cache_hits + self._cache_misses
        if total:
            pct = round(100.0 * self._cache_hits / total, 1)
            logging.info(
                "Parse cache: %d/%d hits (%.1f%%), %d miss%s.",
                self._cache_hits, total, pct, self._cache_misses,
                "" if self._cache_misses == 1 else "es",
            )

    def _needs_update(self, dir_path: str, files: List[str]) -> bool:
        map_file_path = os.path.join(dir_path, self.map_filename)

        if not os.path.exists(map_file_path):
            return True

        try:
            map_mtime = os.path.getmtime(map_file_path)

            # 1. Проверяем, редактировали ли существующие файлы
            for f in files:
                file_path = os.path.join(dir_path, f)
                if os.path.getmtime(file_path) > map_mtime:
                    return True

            # 2. ФИКС: Проверяем, не удалили ли (или добавили) файлы
            with open(map_file_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
                old_files = set(old_data.get("files", {}).keys())
                current_files = set(files)

                if old_files != current_files:
                    return True # Состав файлов изменился, нужно обновить

        except (OSError, json.JSONDecodeError) as e:
            logging.warning(f"Error reading metadata in {dir_path}: {e}")
            return True # Если что-то сломалось, надежнее просто обновить

        return False

    # ------------------------------------------------------------------
    # Aggregations: roles + symbol index
    # ------------------------------------------------------------------

    def _iter_all_module_maps(self):
        """Yield ``(map_path, parsed_json)`` for every ``_module_map.json``
        currently sitting on disk under ``root_dir``. The set may be a
        superset of files we just wrote (caches that didn't need updating
        this run still count) — exactly the right behaviour for building
        an aggregate index.
        """
        for cur, dirs, files in os.walk(self.root_dir):
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs]
            if self.map_filename not in files:
                continue
            mp = os.path.join(cur, self.map_filename)
            try:
                with open(mp, 'r', encoding='utf-8') as fh:
                    yield mp, json.load(fh)
            except (OSError, json.JSONDecodeError) as e:
                logging.warning(f"Skipping unreadable map {mp}: {e}")

    @staticmethod
    def _rel_norm(file_path: str, root_dir: str) -> str:
        """Stable relpath with forward slashes, no leading './'."""
        try:
            rel = os.path.relpath(file_path, root_dir)
        except ValueError:
            rel = file_path
        rel = rel.replace(os.sep, '/')
        while rel.startswith('./'):
            rel = rel[2:]
        return rel

    def _build_root_map(self) -> None:
        root_map_path = os.path.join(self.root_dir, self.root_map_filename)

        # Aggregate roles across every module map. We re-read maps from
        # disk (vs holding state in memory) so this works even when most
        # directories were skipped via the mtime cache.
        roles: Dict[str, List[str]] = {}
        seen_per_role: Dict[str, set] = {}

        for _mp, data in self._iter_all_module_maps():
            files = data.get("files", {})
            for _fname, fdata in files.items():
                exports = fdata.get("exports", []) or []
                for exp in exports:
                    if not isinstance(exp, dict):
                        continue
                    role = exp.get("role")
                    name = exp.get("name")
                    if not role or not name:
                        continue
                    bucket = roles.setdefault(role, [])
                    seen = seen_per_role.setdefault(role, set())
                    if name in seen:
                        continue
                    seen.add(name)
                    bucket.append(name)

        for r in roles:
            roles[r].sort()

        root_data = {
            "project_root": os.path.abspath(self.root_dir),
            "modules": self.processed_modules,
            "entry_instruction": (
                f"Read {self.readme_filename} first, then navigate modules "
                f"via {self.map_filename}."
            ),
            # Lets agents know which extra artifacts the builder
            # produced — they're free to ignore the ones they don't
            # support, but at least they don't have to probe for them.
            "artifacts": [
                self.symbols_filename,
                self.tests_filename,
                self.routes_filename,
                self.callbacks_filename,
                self.fsm_flow_filename,
                self.test_categories_filename,
            ],
        }
        if roles:
            # Emit roles in a deterministic key order so diffs stay clean.
            root_data["roles"] = {k: roles[k] for k in sorted(roles)}

        try:
            with open(root_map_path, 'w', encoding='utf-8') as f:
                json.dump(root_data, f, indent=2)
                f.write("\n")
        except IOError as e:
            logging.error(f"Failed to write root map: {e}")

    def _build_symbol_index(self) -> None:
        """Walk every ``_module_map.json`` and emit a project-wide
        ``agent_symbols.json``: ``{symbol_name → {file, kind, params, doc, role}}``.

        Collision rule: when the same symbol name lives in multiple
        files, prefer the **shortest path** (likely the canonical
        definition over a re-export). Tie-break alphabetically on file
        path to keep builds deterministic.
        """
        index: Dict[str, Dict[str, str]] = {}

        for _mp, data in self._iter_all_module_maps():
            directory = data.get("directory") or "."
            files = data.get("files", {})
            for fname, fdata in files.items():
                # Skip non-Python entries that emit string exports
                # (Dockerfile / docker-compose) — those aren't symbols.
                if not isinstance(fdata, dict):
                    continue
                file_rel = self._rel_norm(
                    os.path.join(directory, fname), self.root_dir
                )
                exports = fdata.get("exports", []) or []
                for exp in exports:
                    if not isinstance(exp, dict):
                        continue
                    name = exp.get("name")
                    if not name:
                        continue

                    candidate: Dict[str, str] = {"file": file_rel}
                    for k in ("kind", "params", "doc", "role", "inputs", "outputs"):
                        v = exp.get(k)
                        if v:
                            candidate[k] = v

                    existing = index.get(name)
                    if existing is None:
                        index[name] = candidate
                        continue

                    # Resolve collision: shortest-path wins; tie-break
                    # alphabetical for determinism.
                    new_path = candidate["file"]
                    old_path = existing["file"]
                    new_score = (len(new_path), new_path)
                    old_score = (len(old_path), old_path)
                    if new_score < old_score:
                        index[name] = candidate

        # Sort keys for deterministic output (idempotent builds).
        ordered = {k: index[k] for k in sorted(index)}
        out_path = os.path.join(self.root_dir, self.symbols_filename)
        try:
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(ordered, f, indent=2, ensure_ascii=False)
                f.write("\n")
            logging.info(
                "Wrote symbol index: %s (%d symbols).", self.symbols_filename, len(ordered)
            )
        except IOError as e:
            logging.error(f"Failed to write symbol index: {e}")

    # ------------------------------------------------------------------
    # Feature B — test linking artifact
    # ------------------------------------------------------------------

    def _build_test_index(self) -> None:
        """Read agent_symbols.json, link each symbol to nearest test, write
        agent_tests.json. Empty index is still emitted so consumers can
        rely on the file's presence after a successful build.
        """
        symbols_path = os.path.join(self.root_dir, self.symbols_filename)
        try:
            with open(symbols_path, 'r', encoding='utf-8') as fh:
                symbols = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            logging.warning(f"Skipping test index: {e}")
            return
        try:
            index = build_test_index(self.root_dir, symbols)
            write_test_index(self.root_dir, index)
            with_test = sum(1 for v in index.values() if v)
            logging.info(
                "Wrote test index: %s (%d/%d symbols linked).",
                self.tests_filename, with_test, len(index),
            )
        except OSError as e:
            logging.error(f"Failed to write test index: {e}")

    # ------------------------------------------------------------------
    # Feature C — cross-language route bridge
    # ------------------------------------------------------------------

    def _build_route_index(self) -> None:
        try:
            index = build_route_index(self.root_dir)
            write_route_index(self.root_dir, index)
            logging.info(
                "Wrote route index: %s (%d route(s)).",
                self.routes_filename, len(index),
            )
        except OSError as e:
            logging.error(f"Failed to write route index: {e}")

    # ------------------------------------------------------------------
    # Feature R — Angular RouterModule path→component map.
    # ------------------------------------------------------------------

    def _build_ng_route_index(self) -> None:
        try:
            routes = build_ng_route_index(self.root_dir)
            # Skip the artifact entirely on non-Angular projects so we
            # don't pollute the file tree with an empty list.
            if not routes:
                return
            write_ng_route_index(self.root_dir, routes)
            logging.info(
                "Wrote Angular route index: %s (%d route(s)).",
                self.ng_routes_filename, len(routes),
            )
        except OSError as e:
            logging.error(f"Failed to write Angular route index: {e}")

    # ------------------------------------------------------------------
    # Feature D — aiogram callback_data index
    # ------------------------------------------------------------------

    def _build_callback_index(self) -> None:
        try:
            index = collect_callbacks(self.root_dir)
            write_callback_index(self.root_dir, index)
            logging.info(
                "Wrote callback index: %s (%d entries).",
                self.callbacks_filename, len(index),
            )
        except OSError as e:
            logging.error(f"Failed to write callback index: {e}")

    # ------------------------------------------------------------------
    # Feature F — aiogram FSM flow graph
    # ------------------------------------------------------------------

    def _build_fsm_flow_index(self) -> None:
        try:
            index = collect_fsm_flow(self.root_dir)
            write_fsm_flow(self.root_dir, index)
            logging.info(
                "Wrote FSM flow index: %s (%d state(s)).",
                self.fsm_flow_filename, len(index),
            )
        except OSError as e:
            logging.error(f"Failed to write FSM flow index: {e}")

    # ------------------------------------------------------------------
    # Feature H — test categorisation (unit / integration / unknown)
    # ------------------------------------------------------------------

    def _build_test_categories_index(self) -> None:
        try:
            index = collect_test_categories(self.root_dir)
            write_test_categories(self.root_dir, index)
            # Inline summary so the build log shows the unit/integration
            # split without requiring a follow-up CLI call.
            from test_classifier import category_summary  # type: ignore[import-not-found]
            summary = category_summary(index)
            summary_text = ", ".join(f"{k}={v}" for k, v in sorted(summary.items()))
            logging.info(
                "Wrote test categories: %s (%d files; %s).",
                self.test_categories_filename, len(index), summary_text or "empty",
            )
        except OSError as e:
            logging.error(f"Failed to write test categories: {e}")

    # ------------------------------------------------------------------
    # Feature I — locale-key index (i18n strings as queryable data)
    # ------------------------------------------------------------------

    def _build_locale_index(self) -> None:
        try:
            # Allow conventions.json to override the locales path.
            locales_dir = "locales"
            conv_path = os.path.join(self.root_dir, ".vc-context", "conventions.json")
            if os.path.exists(conv_path):
                try:
                    with open(conv_path, "r", encoding="utf-8") as fh:
                        conv = json.load(fh)
                    override = (
                        conv.get("locales", {}).get("path")
                        if isinstance(conv, dict) else None
                    )
                    if isinstance(override, str) and override:
                        locales_dir = override
                except (OSError, json.JSONDecodeError):
                    pass
            index = build_locale_index(self.root_dir, locales_dir=locales_dir)
            if not index:
                # No locales — keep silent. Most projects don't have them
                # and emitting an empty file would just clutter the tree.
                return
            write_locale_index(self.root_dir, index)
            # Surface parity gaps in the build log so missing translations
            # don't ship unnoticed.
            missing_total = sum(len(v.get("missing", [])) for v in index.values())
            logging.info(
                "Wrote locale index: %s (%d keys; %d missing translations).",
                self.locales_filename, len(index), missing_total,
            )
        except OSError as e:
            logging.error(f"Failed to write locale index: {e}")

    def _generate_agent_sop(self) -> None:
        """Generates Standard Operating Procedure for AI Agents."""
        readme_path = os.path.join(self.root_dir, self.readme_filename)
        # Always rewrite — the SOP is part of the context-graph contract,
        # not a hand-edited doc. If you want extra notes, keep them in
        # README.md (separate file).

        content = (
            "# 🤖 Agent SOP — read this first, every time\n\n"
            "This repo ships hierarchical context graphs so you can answer\n"
            "questions and edit code WITHOUT loading the full source tree.\n\n"
            "## Cardinal rule: read narrowly, write fully\n\n"
            f"**Five artifacts, five purposes — pick the right one first.**\n\n"
            f"- `{self.root_map_filename}` — directory list + `roles` aggregator\n"
            "  + `artifacts` listing (\"all routes / migrations / scheduler\n"
            "  jobs / webhooks\").\n"
            f"- `{self.symbols_filename}` — flat `{{name → {{file, kind, params,\n"
            "  doc, role, test}}}}` index. **O(1) lookup** for \"where is\n"
            "  symbol X?\"\n"
            f"- `{self.tests_filename}` — `{{symbol → {{test_file,\n"
            "  test_function, line} | null}}` map. Use to find the nearest\n"
            "  existing test for a symbol you're about to change.\n"
            f"- `{self.routes_filename}` — `{{path → {{method, handler, file,\n"
            "  callers_js}}}}` cross-language bridge. Use to see which JS/TS\n"
            "  call-sites depend on a backend route before you change its\n"
            "  shape.\n"
            f"- `<dir>/{self.map_filename}` — per-folder zoom-in: every export\n"
            "  in every file with shape and own-package deps.\n\n"
            f"### Step 0 — \"show me all <role>\" queries\n"
            f"   If the user asks \"list all routes / migrations / scheduler\n"
            f"   jobs / repositories / services / api-clients / webhooks\", read\n"
            f"   `{self.root_map_filename}` and use its `roles` section.\n"
            f"   No folder iteration needed.\n\n"
            f"1. **Start tiny.** Read `{self.root_map_filename}` (one short JSON,\n"
            "   ~few hundred tokens). It lists every module folder + the\n"
            "   `roles` aggregator. **Stop here if the question is structural**\n"
            "   (\"where is X handled?\", \"what modules exist?\"). Don't fetch\n"
            "   maps preemptively.\n\n"
            f"### Step 1.5 — \"where is symbol X defined?\"\n"
            f"   Before opening any module map, check `{self.symbols_filename}`.\n"
            "   It's a single flat dict — one read, one lookup. The value\n"
            "   tells you the file, kind, signature, docstring summary, and\n"
            "   role tag. Only zoom into the module map if you need to see\n"
            "   that file's neighbours.\n\n"
            f"2. **Zoom in.** When you know which folder you need, read\n"
            f"   `<that-folder>/{self.map_filename}` — and only that one. Each map\n"
            "   lists every file's public exports (name + kind + signature +\n"
            "   docstring summary + optional `role`) and its own-package\n"
            "   dependencies. That is usually enough to answer \"is there\n"
            "   already a function for X?\" or \"what does file Y expose?\".\n\n"
            "3. **Open source only when editing or when summary is insufficient.**\n"
            "   The map shows shapes; the file holds the body. Don't open a\n"
            "   `.py` file just to check what it imports — the map already says.\n\n"
            "4. **Never read more than 1–3 maps in a row** before making a\n"
            "   reading-vs-acting decision. Loading every `_module_map.json`\n"
            "   defeats the point: tens of thousands of tokens of dependency\n"
            "   data when you needed two functions.\n\n"
            "## Role tags (extensible — see .ai-context/symbols.py)\n"
            "Built-in (Python):\n"
            "- `route` — FastAPI HTTP route (`@router.get/post/...`).\n"
            "- `aiogram-handler` — `@router.message(...)` / `@router.callback_query(...)`.\n"
            "- `webhook` — payment/event webhook (best-effort name + signature heuristic).\n"
            "- `migration` — Alembic `upgrade()` / `downgrade()` in `alembic/versions/`.\n"
            "- `scheduler-job` — function name registered via `scheduler.add_job(...)`.\n"
            "- `repository` — every export from `database/repositories/*.py`.\n"
            "- `service` — every export from `services/*.py`.\n"
            "- `api-client` — every export from `bot/api_client/*.py`.\n\n"
            "Built-in (JS/TS):\n"
            "- `react-component` — capitalised function in `.jsx` / `.tsx` returning JSX.\n"
            "- `react-hook` — `useFoo` function calling any of `useState`/`useEffect`/...\n"
            "- `express-route` — handler registered via `app.<verb>(...)` / `router.<verb>(...)`.\n"
            "- `vue-composable` — function under `composables/` whose name starts with `use`.\n\n"
            "Custom (project-declared via `.vc-context/roles.json`):\n"
            "- The full live vocabulary lives in `agent_root.json.roles` —\n"
            "  read those keys, not this list, when in doubt.\n\n"
            "## Action-tier queries (need to *do* something, not just look up)\n"
            "- \"Did I break a project rule?\" →\n"
            "  MCP `lint_violations` or CLI `vc-context lint`. Reads\n"
            "  `.vc-context/conventions.json` at the parent project root.\n"
            "  Empty list when the file is absent — opt-in.\n"
            "- \"Where should I add a test for symbol X?\" →\n"
            "  MCP `find_test(symbol=X)` or CLI `vc-context test X`. Returns\n"
            "  the nearest existing test so you can colocate the new case.\n"
            f"  See `{self.tests_filename}` for the prebuilt map; the live\n"
            "  fallback handles fresh symbols.\n"
            "- \"What JS code calls this backend route?\" →\n"
            "  MCP `route_callers(path)` or CLI\n"
            "  `vc-context route-callers /api/foo`. Lists every JS/TS call-\n"
            "  site that hits the route. Use BEFORE changing a route's\n"
            "  contract.\n\n"
            "## What you will NOT find in maps\n"
            "- Stdlib / third-party imports (filtered out as noise).\n"
            "- Private (`_prefixed`) helpers or nested defs.\n"
            "- Empty `__init__.py` files or pure-glue modules.\n"
            "If you need those — read the source.\n\n"
            "## Regenerating the maps\n"
            "Maps refresh automatically on every `git commit` (pre-commit\n"
            "hook). If you bypassed the hook, run:\n"
            f"```\npython3 .ai-context/agent_map.py\n```\n\n"
            f"**Never hand-edit `{self.map_filename}`, `{self.root_map_filename}`, or\n"
            f"`{self.symbols_filename}`** — the next commit will overwrite\n"
            "your changes. The symbol index is auto-generated.\n"
        )
        try:
            with open(readme_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logging.info(f"Generated Agent SOP: {self.readme_filename}")
        except IOError as e:
            logging.error(f"Failed to write {self.readme_filename}: {e}")

if __name__ == "__main__":
    builder = ContextBuilder()
    builder.run()
