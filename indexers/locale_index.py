"""Locale-key index — surface i18n strings as first-class queryable data.

Many projects (this one included) carry hundreds of translation keys
across ``locales/<lang>/<namespace>.json`` files. Without an index,
every "what's the message for X?" or "is key Y missing in en?"
question turns into a recursive grep across JSON.

Layout assumption (default): ``<project>/locales/<lang>/<ns>.json``.
The path can be overridden via ``.vc-context/conventions.json``::

    {
        "locales": { "path": "locales" }
    }

Output artifact ``agent_locale_keys.json`` shape::

    {
        "<key>": {
            "namespace": "admin",
            "languages": ["en", "uk"],
            "values":    {"en": "...", "uk": "..."},
            "missing":   []  // languages that have the namespace
                             // file but don't carry this key
        }
    }

``missing`` lets the parity-check use case stay one query away —
"which uk keys aren't translated to en yet?" → list every entry
where ``"en" in missing``.

Stdlib only.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

LOCALES_FILENAME = "agent_locale_keys.json"
DEFAULT_LOCALES_DIR = "locales"


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    """Read a JSON file. Return None on missing/parse failure — the
    builder should keep going across other files."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _scan_layout(
    project_root: str,
    locales_dir: str = DEFAULT_LOCALES_DIR,
) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Walk ``<root>/<locales_dir>/<lang>/<ns>.json`` and return
    nested ``{lang: {namespace: {key: value}}}``.

    Files that aren't JSON, aren't dicts, or contain non-string
    values are skipped silently — i18n files normally are flat str
    dicts, anything else is project-internal data that doesn't
    belong in the locale index.
    """
    base = os.path.join(project_root, locales_dir)
    if not os.path.isdir(base):
        return {}

    out: Dict[str, Dict[str, Dict[str, str]]] = {}
    for lang in sorted(os.listdir(base)):
        lang_dir = os.path.join(base, lang)
        if not os.path.isdir(lang_dir):
            continue
        for fname in sorted(os.listdir(lang_dir)):
            if not fname.endswith(".json"):
                continue
            ns = fname[: -len(".json")]
            data = _read_json(os.path.join(lang_dir, fname))
            if data is None:
                continue
            ns_dict: Dict[str, str] = {}
            for key, value in data.items():
                if isinstance(value, str):
                    ns_dict[key] = value
            if ns_dict:
                out.setdefault(lang, {})[ns] = ns_dict
    return out


def build_locale_index(
    project_root: str,
    locales_dir: str = DEFAULT_LOCALES_DIR,
) -> Dict[str, Dict[str, Any]]:
    """Build the ``{key → entry}`` map for ``agent_locale_keys.json``.

    ``locales_dir`` is the project-relative path to the locales root
    (overridden by ``conventions.json["locales"]["path"]``).
    """
    layout = _scan_layout(project_root, locales_dir)
    if not layout:
        return {}

    # Map ns → set of langs that have a file for that namespace.
    # Used to compute the "missing" list correctly: if a language
    # doesn't even have the admin.json file, every admin key is
    # missing for that language.
    ns_owners: Dict[str, set] = {}
    for lang, ns_data in layout.items():
        for ns in ns_data:
            ns_owners.setdefault(ns, set()).add(lang)

    index: Dict[str, Dict[str, Any]] = {}
    for lang, ns_data in layout.items():
        for ns, kv in ns_data.items():
            for key, value in kv.items():
                entry = index.setdefault(
                    key,
                    {
                        "namespace": ns,
                        "languages": [],
                        "values": {},
                        "missing": [],
                    },
                )
                # If the same key appears in multiple namespaces
                # (unusual but possible), the first-seen one wins
                # for the canonical 'namespace' field; values still
                # collect across langs.
                entry["values"][lang] = value
                if lang not in entry["languages"]:
                    entry["languages"].append(lang)

    # Compute 'missing' per entry: for the entry's namespace, which
    # languages own the namespace file but don't carry this key?
    for entry in index.values():
        owners = ns_owners.get(entry["namespace"], set())
        present = set(entry["languages"])
        entry["missing"] = sorted(owners - present)
        entry["languages"].sort()
    return index


def write_locale_index(project_root: str, index: Dict[str, Any]) -> str:
    """Persist ``agent_locale_keys.json`` and return its absolute path."""
    from paths import ensure_index_dir, index_path

    ensure_index_dir(project_root)
    out_path = index_path(project_root, LOCALES_FILENAME)
    ordered = {k: index[k] for k in sorted(index)}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return out_path


# ----------------------------------------------------------------------
# Read-side queries — used by query_engine + CLI + MCP.
# ----------------------------------------------------------------------


def list_keys(
    index: Dict[str, Dict[str, Any]],
    namespace: Optional[str] = None,
) -> List[str]:
    """Return all keys, optionally filtered to one namespace."""
    if namespace:
        return sorted(k for k, v in index.items() if v.get("namespace") == namespace)
    return sorted(index.keys())


def find_keys(
    index: Dict[str, Dict[str, Any]],
    pattern: str,
    case_insensitive: bool = True,
) -> List[str]:
    """Substring match across keys. Empty pattern returns nothing —
    callers should use :func:`list_keys` for a full dump."""
    if not pattern:
        return []
    needle = pattern.lower() if case_insensitive else pattern
    out = []
    for key in index:
        hay = key.lower() if case_insensitive else key
        if needle in hay:
            out.append(key)
    return sorted(out)


def get_key(
    index: Dict[str, Dict[str, Any]],
    key: str,
) -> Optional[Dict[str, Any]]:
    """Full entry for one key, or None when missing."""
    entry = index.get(key)
    return entry


def find_drift(
    index: Dict[str, Dict[str, Any]],
    namespace: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return every key whose ``missing`` list is non-empty — a parity
    audit hook.

    A key drifts when it's present in one language file but absent in
    a sibling language that owns the same namespace file. The
    underlying ``missing`` field is already computed by
    :func:`build_locale_index`; this is the public roll-up.

    Optional ``namespace`` scopes the audit to one namespace
    (``"common"`` / ``"admin"`` / etc.).

    Each record: ``{key, namespace, present, missing}``, sorted by
    ``(namespace, key)`` for stable diffing.
    """
    out: List[Dict[str, Any]] = []
    for key, entry in index.items():
        if namespace and entry.get("namespace") != namespace:
            continue
        missing = entry.get("missing") or []
        if not missing:
            continue
        out.append(
            {
                "key": key,
                "namespace": entry.get("namespace"),
                "present": sorted(entry.get("languages") or []),
                "missing": list(missing),
            }
        )
    out.sort(key=lambda r: (r["namespace"] or "", r["key"]))
    return out
