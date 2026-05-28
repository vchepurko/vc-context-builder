"""Build and query a small symbol impact graph.

The graph answers the inverse-dependency question: "if this symbol
changes, which symbols are most likely affected?"  It is intentionally
conservative and stdlib-only.  Edges are derived from data the indexer
already captures:

* ``symbol.callees``: ``callee -> caller``
* per-file ``dependencies``: imported symbol/module -> exports in that file
* ``agent_tests.json``: impacted symbols -> tests at risk
"""

from __future__ import annotations

import json
import os
from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Set

from paths import ensure_index_dir, index_path

IMPACT_FILENAME = "agent_impact.json"


def _normalise_path(path: str) -> str:
    return path.replace(os.sep, "/").lstrip("./")


def _symbol_module(file_path: str) -> str:
    rel = _normalise_path(file_path)
    root, _ = os.path.splitext(rel)
    init_suffix = "/__init__"
    if root.endswith(init_suffix):
        root = root[: -len(init_suffix)]
    return root.replace("/", ".")


def _candidate_dependency_names(dep: str) -> Set[str]:
    dep = dep.strip()
    if not dep:
        return set()
    names = {dep}
    dotted = dep.replace("/", ".").removesuffix(".py")
    names.add(dotted)
    names.add(dotted.rsplit(".", 1)[-1])
    return {n for n in names if n}


def _add_edge(graph: Dict[str, Set[str]], source: str, target: str) -> None:
    if not source or not target or source == target:
        return
    graph.setdefault(source, set()).add(target)


def _iter_module_maps(project_root: str) -> Iterable[Dict[str, Any]]:
    for cur, dirs, files in os.walk(project_root):
        dirs[:] = [
            d
            for d in dirs
            if d
            not in {
                ".git",
                ".vc-context",
                ".ai-context",
                "node_modules",
                "vendor",
                "__pycache__",
                "dist",
                "build",
                ".venv",
                "venv",
            }
        ]
        if "_module_map.json" not in files:
            continue
        try:
            with open(os.path.join(cur, "_module_map.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            yield data


def build_impact_graph(
    project_root: str,
    symbols: Dict[str, Dict[str, Any]],
    tests: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the serialisable ``agent_impact.json`` payload."""
    graph: Dict[str, Set[str]] = {name: set() for name in symbols}

    module_to_symbols: Dict[str, Set[str]] = {}
    basename_to_symbols: Dict[str, Set[str]] = {}
    file_to_symbols: Dict[str, Set[str]] = {}
    for name, rec in symbols.items():
        file_path = str(rec.get("file") or "")
        if not file_path:
            continue
        file_rel = _normalise_path(file_path)
        file_to_symbols.setdefault(file_rel, set()).add(name)
        module = _symbol_module(file_rel)
        module_to_symbols.setdefault(module, set()).add(name)
        basename_to_symbols.setdefault(module.rsplit(".", 1)[-1], set()).add(name)

    # AST-derived call edges: changing a callee can affect its callers.
    for caller, rec in symbols.items():
        for callee in rec.get("callees") or []:
            if isinstance(callee, str) and callee in symbols:
                _add_edge(graph, callee, caller)

    # File dependency edges: an imported symbol/module can affect every
    # export in the importing file.
    for module_map in _iter_module_maps(project_root):
        directory = str(module_map.get("directory") or "").strip("/")
        directory = "" if directory == "." else directory.lstrip("./")
        files = module_map.get("files") or {}
        if not isinstance(files, dict):
            continue
        for fname, fdata in files.items():
            if not isinstance(fdata, dict):
                continue
            file_rel = _normalise_path(f"{directory}/{fname}" if directory else fname)
            targets: Set[str] = set()
            for exp in fdata.get("exports") or []:
                if not isinstance(exp, dict):
                    continue
                exp_name = exp.get("name")
                if isinstance(exp_name, str) and exp_name in symbols:
                    targets.add(exp_name)
            if not targets:
                targets = file_to_symbols.get(file_rel, set())
            for dep in fdata.get("dependencies") or []:
                if not isinstance(dep, str):
                    continue
                sources: Set[str] = set()
                for candidate in _candidate_dependency_names(dep):
                    if candidate in symbols:
                        sources.add(candidate)
                    sources.update(module_to_symbols.get(candidate, set()))
                    sources.update(basename_to_symbols.get(candidate, set()))
                for source in sources:
                    for target in targets:
                        _add_edge(graph, source, target)

    tests = tests or {}
    payload: Dict[str, Any] = {
        "version": 1,
        "symbols": {},
    }
    for name in sorted(symbols):
        rec = symbols[name]
        payload["symbols"][name] = {
            "file": rec.get("file"),
            "line": rec.get("line"),
            "direct": sorted(graph.get(name, set())),
            "test": tests.get(name),
            "template_refs": [],
        }
    return payload


def write_impact_graph(project_root: str, graph: Dict[str, Any]) -> None:
    ensure_index_dir(project_root)
    out = index_path(project_root, IMPACT_FILENAME)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def query_impact(
    graph: Dict[str, Any],
    symbol: str,
    *,
    depth: int = 2,
) -> Optional[Dict[str, Any]]:
    """Return direct/indirect impact for ``symbol`` from a graph payload."""
    symbols = graph.get("symbols") if isinstance(graph, dict) else None
    if not isinstance(symbols, dict) or symbol not in symbols:
        return None

    depth = max(1, min(5, int(depth)))
    direct = list(symbols.get(symbol, {}).get("direct") or [])
    seen: Set[str] = {symbol}
    queue = deque((name, [symbol, name], 1) for name in direct)
    indirect_paths: List[str] = []
    impacted: Set[str] = set(direct)

    while queue:
        current, path, level = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        impacted.add(current)
        if level > 1:
            indirect_paths.append(" -> ".join(path))
        if level >= depth:
            continue
        for nxt in symbols.get(current, {}).get("direct") or []:
            if nxt not in seen:
                queue.append((nxt, [*path, nxt], level + 1))

    tests_at_risk: List[Dict[str, Any]] = []
    tests_seen: Set[tuple] = set()
    for name in sorted(impacted):
        test = symbols.get(name, {}).get("test")
        if not isinstance(test, dict):
            continue
        key = (
            test.get("test_file"),
            test.get("test_function"),
            test.get("line"),
        )
        if key in tests_seen:
            continue
        tests_seen.add(key)
        tests_at_risk.append({"symbol": name, **test})

    template_refs: List[Dict[str, Any]] = []
    for name in sorted(impacted):
        refs = symbols.get(name, {}).get("template_refs") or []
        for ref in refs:
            if isinstance(ref, dict):
                template_refs.append({"symbol": name, **ref})

    return {
        "symbol": symbol,
        "depth": depth,
        "direct": direct,
        "indirect": indirect_paths,
        "tests_at_risk": tests_at_risk,
        "template_refs": template_refs,
    }
