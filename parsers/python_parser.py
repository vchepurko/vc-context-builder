import ast
from typing import Dict, List, Optional, Set

from parsers.base_parser import BaseParser

try:
    # Available when run as a package or with .ai-context on sys.path
    # (which is the case via agent_map.py).
    from symbols import (
        extract_decorator_roles,
        is_webhook_function,
        path_role,
    )
except Exception:  # pragma: no cover — graceful degradation
    extract_decorator_roles = lambda _node: None  # type: ignore
    is_webhook_function = lambda _node: False  # type: ignore
    path_role = lambda _p: None  # type: ignore


class PythonParser(BaseParser):
    """AST-based parser for Python files.

    Per-file output:
      exports:      [{name, kind, params?, doc?, role?}]   ← top-level only,
                       no _-private, with signature + docstring summary
      dependencies: [str]   ← top-level package names (filtering of
                       stdlib / third-party happens in agent_map.py)

    The optional ``role`` field is a short tag derived from decorator
    patterns or file-path heuristics: ``route`` / ``aiogram-handler`` /
    ``webhook`` / ``migration`` / ``scheduler-job`` / ``repository`` /
    ``service`` / ``api-client``. ``None`` (omitted) when nothing fires.
    """

    extensions = ['.py']

    def extract(
        self,
        file_path: str,
        scheduler_jobs: Optional[Set[str]] = None,
    ) -> Dict[str, List]:
        """Parse a Python file.

        ``scheduler_jobs`` — set of callable names registered via
        ``scheduler.add_job(...)`` anywhere in the codebase, used to tag
        those callables with ``role: scheduler-job``. Pre-computed once
        by ``ContextBuilder`` so we don't re-scan per file.
        """
        content = self._read_file(file_path)
        if not content:
            return {"exports": [], "dependencies": []}

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return {"exports": [], "dependencies": []}

        # File-path-derived role applies to every export from this file
        # (repositories, services, api_client, alembic migrations).
        file_role = path_role(file_path)
        scheduler_jobs = scheduler_jobs or set()

        exports: List[Dict[str, str]] = []
        deps: set = set()
        seen: set = set()

        # Top-level nodes only — nested defs are usually noise for an agent
        # trying to find a public surface to call.
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                if name.startswith("_") and not name.startswith("__"):
                    continue  # private helper, skip
                if name in seen:
                    continue
                seen.add(name)
                exports.append(
                    self._summarise(
                        node,
                        file_role=file_role,
                        scheduler_jobs=scheduler_jobs,
                        source=content,
                    )
                )

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    deps.add(alias.name.split(".")[0])

            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    deps.add(node.module.split(".")[0])

        return {"exports": exports, "dependencies": sorted(deps)}

    @staticmethod
    def _summarise(
        node,
        file_role: Optional[str] = None,
        scheduler_jobs: Optional[Set[str]] = None,
        source: Optional[str] = None,
    ) -> Dict[str, str]:
        kind = (
            "class" if isinstance(node, ast.ClassDef)
            else "async-func" if isinstance(node, ast.AsyncFunctionDef)
            else "func"
        )
        out: Dict[str, str] = {"name": node.name, "kind": kind}

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                out["params"] = "(" + ast.unparse(node.args) + ")"
            except Exception:
                out["params"] = "(...)"

        # Stash decorator + body text for custom_roles regexes. These
        # are private fields (`_`-prefixed) — agent_map.py strips them
        # before writing JSON to disk.
        try:
            decorators = getattr(node, "decorator_list", []) or []
            dec_text = "\n".join(ast.unparse(d) for d in decorators)
            if dec_text:
                out["_decorators_text"] = dec_text
        except Exception:
            pass
        if source is not None:
            try:
                seg = ast.get_source_segment(source, node)
                if seg:
                    out["_body"] = seg
            except Exception:
                pass

        doc = ast.get_docstring(node) or ""
        for line in doc.splitlines():
            line = line.strip()
            if line:
                out["doc"] = line[:120]
                break

        # Role detection — additive, omitted if nothing fires.
        # Priority order (most specific wins):
        #   webhook > aiogram-handler > route > migration > scheduler-job
        # Webhook is more specific than route: a webhook IS a route, but
        # an agent searching "show me all webhooks" wants exactly those.
        role: Optional[str] = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorator_role = extract_decorator_roles(node)
            if is_webhook_function(node):
                # Even if decorated as `@router.post(...)`, prefer the
                # more meaningful `webhook` tag for agent navigation.
                role = "webhook"
            elif decorator_role is not None:
                role = decorator_role
            # Alembic upgrade()/downgrade() functions live in versions/.
            if role is None and file_role == "migration" and node.name in {"upgrade", "downgrade"}:
                role = "migration"
            # Scheduler-job tagging via cross-codebase scan.
            if role is None and scheduler_jobs and node.name in scheduler_jobs:
                role = "scheduler-job"

        # Path-derived fallback (repository / service / api-client).
        # `migration` for non-upgrade/downgrade exports in versions/ would
        # be misleading — keep those untagged so the role pool stays tight.
        if role is None and file_role and file_role != "migration":
            role = file_role

        if role:
            out["role"] = role

        return out
