"""Output formatters for the ``vc-context`` CLI.

Pure presentation — no engine logic, no argparse. Each ``_print_*``
function takes the QueryEngine result shape directly and writes a
human-readable rendering to stdout. ``_emit_json`` is the JSON
fallback used when ``--json`` is set.

Kept separate from ``cli.py`` so the entry script stays small enough
to read in one screenful, and from ``cli_handlers.py`` so the
handlers stay focused on engine wiring.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List


def _emit_json(payload: Any) -> None:
    """Serialise to stdout in the canonical CLI ``--json`` shape."""
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, sort_keys=False)
    sys.stdout.write("\n")


def _print_symbol(name: str, entry: Dict[str, Any]) -> None:
    print(f"{name}")
    # File + line range come first — most agents/devs need them next.
    file_v = entry.get("file")
    if file_v:
        line_v = entry.get("line")
        end_v = entry.get("end_line")
        suffix = ""
        if line_v:
            suffix = f":{line_v}"
            if end_v and end_v != line_v:
                suffix += f"-{end_v}"
        print(f"  file: {file_v}{suffix}")
    for key in ("kind", "params", "role"):
        if entry.get(key):
            print(f"  {key}: {entry[key]}")
    if entry.get("doc"):
        print("  doc:")
        for line in str(entry["doc"]).splitlines():
            print(f"    {line}")
    test = entry.get("test")
    if isinstance(test, dict) and test.get("test_file"):
        test_line = test.get("line")
        suffix = f":{test_line}" if test_line else ""
        print(f"  test: {test.get('test_file')}{suffix}  ({test.get('test_function')})")
    # Fact fields only show when explicitly requested via --fields
    # (HIDE_BY_DEFAULT keeps them off the lean response).
    callees = entry.get("callees")
    if callees:
        print(
            f"  callees ({len(callees)}): {', '.join(callees[:8])}"
            + (" ..." if len(callees) > 8 else "")
        )
    raises = entry.get("raises")
    if raises:
        print(f"  raises: {', '.join(raises)}")
    body = entry.get("body")
    if body:
        print("  body:")
        for line in str(body).splitlines():
            print(f"    {line}")


def _print_callers(symbol: str, callers: List[Dict[str, str]]) -> None:
    if not callers:
        print(f"No callers found for {symbol}.")
        return
    print(f"{len(callers)} possible caller(s) for {symbol}:")
    for entry in callers:
        kind = entry.get("kind") or "file"
        print(f"  {entry['file']}  [{kind}]")


def _print_role(role: str, names: List[str]) -> None:
    if not names:
        print(f"No symbols tagged with role '{role}'.")
        return
    print(f"{len(names)} symbol(s) with role '{role}':")
    for n in names:
        print(f"  {n}")


def _print_module(folder: str, summary: Dict[str, Any]) -> None:
    print(f"Module: {summary.get('directory') or folder}")
    files = summary.get("files") or {}
    if not files:
        print("  (no files indexed)")
        return
    for fname, fdata in files.items():
        print(f"  {fname}")
        deps = fdata.get("dependencies") or []
        if deps:
            print(f"    dependencies: {', '.join(deps)}")
        for exp in fdata.get("exports") or []:
            if not isinstance(exp, dict):
                print(f"    {exp}")
                continue
            tag = exp.get("kind") or ""
            role = f" ({exp['role']})" if exp.get("role") else ""
            doc = exp.get("doc")
            line = f"    - {exp.get('name')} [{tag}]{role}"
            if doc:
                line += f"  -- {doc}"
            print(line)


def _print_roles(counts: Dict[str, int]) -> None:
    if not counts:
        print("No roles indexed.")
        return
    width = max(len(r) for r in counts)
    for role in sorted(counts):
        print(f"  {role.ljust(width)}  {counts[role]}")


def _print_modules(modules: List[str]) -> None:
    if not modules:
        print("No modules indexed.")
        return
    for m in modules:
        print(f"  {m}")


def _print_violations(violations: List[Dict[str, Any]]) -> None:
    if not violations:
        print("No violations.")
        return
    # Group by rule_id for readability.
    by_rule: Dict[str, List[Dict[str, Any]]] = {}
    for v in violations:
        by_rule.setdefault(v["rule_id"], []).append(v)
    for rule_id in sorted(by_rule):
        bucket = by_rule[rule_id]
        sev = bucket[0].get("severity", "warn")
        print(f"[{sev.upper()}] {rule_id}  ({len(bucket)} hit(s))")
        for v in bucket:
            print(f"  {v['file']}:{v['line']}  {v['message']}")


def _print_route(entry: Dict[str, Any]) -> None:
    print(f"  path:    {entry.get('path')}")
    print(f"  method:  {entry.get('method')}")
    print(f"  handler: {entry.get('handler')}")
    print(f"  file:    {entry.get('file')}:{entry.get('line')}")
    callers = entry.get("callers_js") or []
    if callers:
        print(f"  callers_js: ({len(callers)})")
        for c in callers:
            print(f"    {c.get('file')}:{c.get('line')}  {c.get('raw')}")
    else:
        print("  callers_js: (none)")
