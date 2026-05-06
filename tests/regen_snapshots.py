#!/usr/bin/env python3
"""Regenerate fixture snapshots committed under ``tests/fixtures/``.

Run after a deliberate change to the MCP tool registry — adding,
removing, or renaming a tool. The committed snapshot is the source of
truth for ``test_tools_list_matches_snapshot``; this script is the
sanctioned way to update it.

Usage:
    python3 tests/regen_snapshots.py            # writes all snapshots
    python3 tests/regen_snapshots.py --check    # exits non-zero if drift

CI can call ``--check`` as a soft gate to remind contributors to
regen, while the snapshot test enforces the actual contract.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBMODULE = os.path.dirname(_HERE)
sys.path.insert(0, _SUBMODULE)

from mcp_server import _tool_specs  # noqa: E402


def _tools_list_snapshot() -> list[str]:
    return sorted(t["name"] for t in _tool_specs())


def _path(name: str) -> str:
    return os.path.join(_HERE, "fixtures", f"{name}.json")


def _write(name: str, payload: object) -> None:
    path = _path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _read(name: str) -> object:
    with open(_path(name), "r", encoding="utf-8") as fh:
        return json.load(fh)


SNAPSHOTS = {
    "tools_list": _tools_list_snapshot,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Don't write — exit non-zero if any snapshot would change.",
    )
    args = parser.parse_args(argv)

    drift: list[str] = []
    for name, builder in SNAPSHOTS.items():
        new = builder()
        if args.check:
            try:
                old = _read(name)
            except FileNotFoundError:
                drift.append(f"{name}: missing on disk")
                continue
            if old != new:
                drift.append(name)
        else:
            _write(name, new)
            print(f"wrote tests/fixtures/{name}.json")

    if args.check:
        if drift:
            print("Snapshots out of date:", ", ".join(drift), file=sys.stderr)
            print("Run: python3 tests/regen_snapshots.py", file=sys.stderr)
            return 1
        print("All snapshots up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
