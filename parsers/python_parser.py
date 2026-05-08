import ast
from collections.abc import Iterator
from typing import Any, Dict, List, Optional, Set

from parsers.base_parser import BaseParser


def _iter_class_body(cls: ast.ClassDef) -> Iterator[ast.AST]:
    """Walk every node inside a class body, including nested defs.

    Used by ``_extract_facts`` so a class's callees / raises represent
    its methods' behaviour, not the class header (decorators, bases,
    keyword args don't carry runtime meaning agents need).
    """
    for stmt in cls.body:
        yield from ast.walk(stmt)


try:
    # Available when run as a package or with .ai-context on sys.path
    # (which is the case via agent_map.py).
    from symbols import (
        extract_decorator_roles,
        is_states_group_class,
        is_webhook_function,
        path_role,
    )
except Exception:  # pragma: no cover — graceful degradation
    extract_decorator_roles = lambda _node: None  # type: ignore
    is_states_group_class = lambda _node: False  # type: ignore
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

    extensions = (".py",)

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
    ) -> Dict[str, Any]:
        kind = (
            "class"
            if isinstance(node, ast.ClassDef)
            else "async-func"
            if isinstance(node, ast.AsyncFunctionDef)
            else "func"
        )
        out: Dict[str, Any] = {"name": node.name, "kind": kind}

        # 1-indexed start/end lines of the def block. `end_lineno` is
        # always set on FunctionDef/ClassDef in 3.8+; defensive fallback
        # for the rare malformed-AST case keeps end >= start.
        line = getattr(node, "lineno", None)
        if isinstance(line, int):
            out["line"] = line
            end_line = getattr(node, "end_lineno", None) or line
            out["end_line"] = end_line

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                out["params"] = "(" + ast.unparse(node.args) + ")"
            except Exception:
                out["params"] = "(...)"

        # Stash decorator + body text for custom_roles regexes. These
        # are private fields (`_`-prefixed) — agent_map.py strips them
        # before writing JSON to disk.
        decorator_names: List[str] = []
        try:
            decorators = getattr(node, "decorator_list", []) or []
            dec_text = "\n".join(ast.unparse(d) for d in decorators)
            if dec_text:
                out["_decorators_text"] = dec_text
            # Also extract decorator names for `get_decorated_with`.
            # ``@cached`` → "cached"; ``@app.get("/x")`` → "app.get";
            # ``@router.message(F.text)`` → "router.message".
            for dec in decorators:
                node_for_name = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(node_for_name, ast.Name):
                    decorator_names.append(node_for_name.id)
                elif isinstance(node_for_name, ast.Attribute):
                    parts: List[str] = [node_for_name.attr]
                    cur: ast.AST = node_for_name.value
                    while isinstance(cur, ast.Attribute):
                        parts.append(cur.attr)
                        cur = cur.value
                    if isinstance(cur, ast.Name):
                        parts.append(cur.id)
                    decorator_names.append(".".join(reversed(parts)))
        except Exception:
            pass
        if decorator_names:
            # De-duplicate while preserving order — straight loop avoids
            # the `seen.add(d) returns None` trick that mypy flags.
            seen_dec: Set[str] = set()
            unique: List[str] = []
            for d in decorator_names:
                if d not in seen_dec:
                    seen_dec.add(d)
                    unique.append(d)
            out["decorators"] = unique
        if source is not None:
            try:
                seg = ast.get_source_segment(source, node)
                if seg:
                    out["_body"] = seg
            except Exception:
                pass

        # AST facts (Tier-1: callees + raises). One body walk per
        # symbol; results are sorted so JSON output is deterministic.
        # These are public fields — find_symbol hides them from its
        # default response (HIDE_BY_DEFAULT in query_engine) but
        # `get_callees` / `get_raised_exceptions` read straight from
        # the symbol record, no extra artifact.
        callees, raises = PythonParser._extract_facts(node)
        if callees:
            out["callees"] = sorted(callees)
        if raises:
            out["raises"] = sorted(raises)

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
        elif isinstance(node, ast.ClassDef):
            # ``class X(StatesGroup)`` → aiogram FSM state group. Tag
            # only the class itself; the field assignments inside aren't
            # top-level exports so they don't appear in the index.
            if is_states_group_class(node):
                role = "fsm-state"

        # Path-derived fallback (repository / service / api-client).
        # `migration` for non-upgrade/downgrade exports in versions/ would
        # be misleading — keep those untagged so the role pool stays tight.
        if role is None and file_role and file_role != "migration":
            role = file_role

        if role:
            out["role"] = role

        return out

    @staticmethod
    def _extract_facts(node: ast.AST) -> tuple:
        """Walk ``node``'s body once, return ``(callees, raises)``.

        Callees: bare ``foo()`` → ``foo``; attribute calls ``a.b.c()`` →
        ``c`` (last attribute, the actual method invoked).  We skip
        ``Call(func=Lambda|Subscript|...)`` — anything that isn't a
        ``Name`` or ``Attribute`` is unlikely to map back to a symbol
        the agent can navigate to.

        Raises: ``raise ValueError(...)`` / ``raise pkg.HTTPError(...)``
        → ``ValueError`` / ``HTTPError``.  Bare ``raise`` (re-raise) and
        ``raise <Name>`` where Name is a local variable still get the
        identifier — we don't try to resolve it; consumers can verify
        with ``find_symbol``.

        Both sets stay deterministic because we sort at the call site.
        """
        callees: set = set()
        raises: set = set()
        # Skip the node itself when it's a class — we only walk
        # contained method bodies for facts (the class header has
        # nothing useful).  For functions we walk the whole body.
        body_iter = ast.walk(node) if not isinstance(node, ast.ClassDef) else _iter_class_body(node)
        for sub in body_iter:
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Name):
                    callees.add(func.id)
                elif isinstance(func, ast.Attribute):
                    callees.add(func.attr)
            elif isinstance(sub, ast.Raise):
                exc = sub.exc
                if exc is None:
                    continue  # bare ``raise`` — nothing to record
                # ``raise SomeExc(...)`` → name of the exception class
                target = exc.func if isinstance(exc, ast.Call) else exc
                if isinstance(target, ast.Name):
                    raises.add(target.id)
                elif isinstance(target, ast.Attribute):
                    raises.add(target.attr)
        return callees, raises
