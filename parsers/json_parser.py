"""Targeted JSON parser — only well-known config files.

Generic JSON would just dump every top-level key as an "export" — pure
noise.  Instead, this parser recognises a small set of conventional
files and extracts the fields an agent actually navigates by:

* ``package.json``  — npm/pnpm/yarn manifest. Exports: ``name@version``;
  dependencies: ``dependencies`` + ``devDependencies`` + ``peerDependencies``
  keys.
* ``tsconfig.json`` — TypeScript config. Exports: declared ``paths``
  aliases (useful for resolving Angular imports); dependencies:
  ``extends`` target + ``baseUrl`` if set.
* ``composer.json`` — PHP package manifest. Exports: ``name``;
  dependencies: ``require`` + ``require-dev`` keys.

Anything else (``agent_root.json``, build artefacts, fixtures) returns
an empty result so it doesn't pollute the index.

Stdlib only.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from parsers.base_parser import BaseParser


class JsonParser(BaseParser):
    """Targeted parser for npm / TS / Composer manifests."""

    # No `extensions` — we don't want every `.json` file matched
    # generically (would flood with config noise).  Filename-based
    # matching keeps the surface small and intentional.
    filenames = ("package.json", "tsconfig.json", "composer.json")

    def extract(self, file_path: str) -> Dict[str, List[str]]:
        content = self._read_file(file_path)
        if not content:
            return {"exports": [], "dependencies": []}
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            logging.warning("JsonParser: invalid JSON in %s: %s", file_path, exc)
            return {"exports": [], "dependencies": []}
        if not isinstance(data, dict):
            return {"exports": [], "dependencies": []}

        basename = os.path.basename(file_path)
        if basename == "package.json":
            return _parse_package_json(data)
        if basename == "tsconfig.json":
            return _parse_tsconfig_json(data)
        if basename == "composer.json":
            return _parse_composer_json(data)
        return {"exports": [], "dependencies": []}


# ----------------------------------------------------------------------
# Per-file extractors — kept module-level so they're importable for
# tests without instantiating the parser.
# ----------------------------------------------------------------------


def _parse_package_json(data: Dict[str, Any]) -> Dict[str, List[str]]:
    exports: List[str] = []
    name = data.get("name")
    version = data.get("version")
    if isinstance(name, str) and name:
        # Combined `name@version` form when both present so the agent
        # can spot version pins at a glance.
        if isinstance(version, str) and version:
            exports.append(f"{name}@{version}")
        else:
            exports.append(name)
    elif isinstance(version, str) and version:
        exports.append(f"@{version}")

    deps: List[str] = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(key)
        if isinstance(block, dict):
            deps.extend(k for k in block if isinstance(k, str))
    return {"exports": exports, "dependencies": sorted(set(deps))}


def _parse_tsconfig_json(data: Dict[str, Any]) -> Dict[str, List[str]]:
    exports: List[str] = []
    deps: List[str] = []

    extends = data.get("extends")
    if isinstance(extends, str) and extends:
        deps.append(extends)

    compiler_opts = data.get("compilerOptions")
    if isinstance(compiler_opts, dict):
        base_url = compiler_opts.get("baseUrl")
        if isinstance(base_url, str) and base_url:
            deps.append(f"baseUrl:{base_url}")
        paths = compiler_opts.get("paths")
        if isinstance(paths, dict):
            # Each path alias (e.g. ``@app/*``) is an "export" — what
            # this tsconfig declares as importable from elsewhere.
            exports.extend(k for k in paths if isinstance(k, str))

    return {"exports": sorted(exports), "dependencies": sorted(set(deps))}


def _parse_composer_json(data: Dict[str, Any]) -> Dict[str, List[str]]:
    exports: List[str] = []
    name = data.get("name")
    if isinstance(name, str) and name:
        exports.append(name)

    deps: List[str] = []
    for key in ("require", "require-dev"):
        block = data.get(key)
        if isinstance(block, dict):
            deps.extend(k for k in block if isinstance(k, str))
    return {"exports": exports, "dependencies": sorted(set(deps))}
