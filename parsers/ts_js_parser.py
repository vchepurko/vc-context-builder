"""Heuristic parser for JavaScript / TypeScript / JSX / TSX.

Goals (parity with the Python parser, give or take):

- Top-level only — no nested function noise.
- Signature capture for both ``function`` statements and arrow-form
  ``const X = (a, b) => …`` declarations.
- JSDoc summaries: take the first non-empty ``*`` line above the
  declaration, strip ``@param`` / ``@returns`` blocks, cap at 120 chars.
- Imports flatten to top-level package names, matching the Python
  parser's behaviour (``import x from '@scope/pkg/sub'`` →  ``@scope``).
- Built-in JS/TS roles:
    * ``react-component``  — ``[A-Z]`` name, ``.jsx`` / ``.tsx`` file,
                             body returns JSX or ``React.createElement(``.
    * ``react-hook``       — name ``^use[A-Z]``, body uses any
                             ``useState/useEffect/useMemo/useCallback/
                             useRef/useContext``.
    * ``express-route``    — the file registers ``app.<verb>(...)`` /
                             ``router.<verb>(...)`` for the symbol.
    * ``vue-composable``   — ``composables/`` directory, name starts
                             with ``use``.

Heuristic, regex-driven, zero deps. The JS/TS world is too gnarly for
a stdlib-only true AST pass — we trade precision for scope.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from parsers.base_parser import BaseParser


# ----------------------------------------------------------------------
# Regex toolbelt
# ----------------------------------------------------------------------

# Match (and remember positions of) decl forms we care about. Anchored
# to the start of a line so we naturally skip indented (nested) defs.
#
# Each pattern captures a name; the extractor then walks forward to
# carve out the body using brace-balancing.

_RE_FUNCTION = re.compile(
    r'^(?P<lead>(?:export\s+(?:default\s+)?)?(?:async\s+)?)'
    r'function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)'
    r'\s*\((?P<params>[^)]*)\)\s*(?::\s*[^{;]+)?\s*\{',
    re.M,
)

# `export default function NAME?(...) {…}` — name optional, treat the
# default export as `default` if anonymous (skipped from output then).
_RE_DEFAULT_FN = re.compile(
    r'^export\s+default\s+(?:async\s+)?function\s*'
    r'(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)?'
    r'\s*\((?P<params>[^)]*)\)\s*(?::\s*[^{;]+)?\s*\{',
    re.M,
)

# `class Foo extends Bar { ... }`
_RE_CLASS = re.compile(
    r'^(?P<lead>(?:export\s+(?:default\s+)?)?(?:abstract\s+)?)'
    r'class\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)'
    r'(?:\s+extends\s+[^\s{]+)?(?:\s+implements\s+[^{]+)?\s*\{',
    re.M,
)

# `const Foo = (...) => {…}` / `const Foo = async (...) => {…}` /
# `const Foo = (...) => expr` — arrow with brace OR expression body.
# We capture ``params`` and a `start_of_body` marker; when the body
# starts with `{` we balance braces, otherwise we read up to the next
# top-level newline / semicolon.
_RE_CONST_ARROW = re.compile(
    r'^(?P<lead>(?:export\s+)?)'
    r'(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)'
    r'(?:\s*:\s*[^=]+)?'
    r'\s*=\s*(?:async\s+)?'
    r'\((?P<params>[^)]*)\)'
    r'(?:\s*:\s*[^=]+?)?'
    r'\s*=>\s*',
    re.M,
)

# `const X = function (...) {…}` — function-expression form.
_RE_CONST_FUNCEXPR = re.compile(
    r'^(?P<lead>(?:export\s+)?)'
    r'(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)'
    r'(?:\s*:\s*[^=]+)?'
    r'\s*=\s*(?:async\s+)?function\s*\*?\s*'
    r'\((?P<params>[^)]*)\)\s*\{',
    re.M,
)

# Imports: `import X from 'pkg'`, `import { X } from 'pkg'`,
# `import * as X from 'pkg'`, `import 'pkg'`, plus `require('pkg')`.
_RE_IMPORT_FROM = re.compile(
    r'^\s*import\s+[^;]*?from\s+["\']([^"\']+)["\']',
    re.M,
)
_RE_IMPORT_BARE = re.compile(
    r'^\s*import\s+["\']([^"\']+)["\']\s*;?',
    re.M,
)
_RE_REQUIRE = re.compile(
    r'\brequire\s*\(\s*["\']([^"\']+)["\']\s*\)',
)

# JSDoc — `/** ... */` block, multi-line.
_RE_JSDOC = re.compile(r'/\*\*([\s\S]*?)\*/')

# Express-style registration site:
#   app.get('/path', handler)
#   router.post('/path', m1, handler)
# We capture the verb (lowered) and the trailing bare-name handler if
# present (for the role-tagging cross-reference).
_RE_EXPRESS_REG = re.compile(
    r'\b(?P<base>[A-Za-z_$][A-Za-z0-9_$]*)'
    r'\.(?P<verb>get|post|put|patch|delete|options|head|all|use)'
    r'\s*\('
    r'\s*[`\'"][^`\'"]+[`\'"]\s*,'
    r'(?P<between>[^)]*?)'
    r'(?P<handler>[A-Za-z_$][A-Za-z0-9_$]*)'
    r'\s*\)',
)

# JSX / React.createElement detection inside a function body.
_RE_JSX_RETURN = re.compile(r'<[A-Za-z][A-Za-z0-9]*')
_RE_REACT_CE = re.compile(r'React\.createElement\s*\(')

# React hook usages — any-of these inside body marks a hook.
_RE_REACT_HOOKS_BODY = re.compile(
    r'\b(useState|useEffect|useMemo|useCallback|useRef|useContext)\s*\('
)


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------

class TsJsParser(BaseParser):
    """Top-level extractor for JS / TS / JSX / TSX files."""

    extensions = ['.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs']

    def extract(self, file_path: str) -> dict[str, Any]:
        content = self._read_file(file_path)
        if not content:
            return {"exports": [], "dependencies": []}

        # Strip comments before scanning for declarations / imports.
        # JSDoc is preserved by capturing it BEFORE this strip via the
        # original-source `_jsdoc_index`.
        jsdoc_index = _index_jsdoc_blocks(content)
        register_sites = list(_RE_EXPRESS_REG.finditer(content))
        scrubbed = _strip_comments(content)

        exports: list[dict[str, Any]] = []
        seen: set = set()

        for entry in self._iter_decls(scrubbed):
            if entry["name"] in seen:
                continue
            seen.add(entry["name"])
            exports.append(entry)

        # Attach JSDoc-derived `doc` field by looking at the source
        # immediately above each declaration's anchor offset.
        for exp in exports:
            anchor = exp.pop("_anchor", None)
            if anchor is None:
                continue
            doc = _jsdoc_for_offset(content, jsdoc_index, anchor)
            if doc:
                exp["doc"] = doc

        # Built-in JS/TS role detection.
        ext = os.path.splitext(file_path)[1].lower()
        norm_path = file_path.replace(os.sep, "/")
        for exp in exports:
            role = _detect_role(exp, ext, norm_path, register_sites, scrubbed)
            if role:
                exp["role"] = role

        # Imports → top-level package names only (no relative imports,
        # no DOM globals, no /wp-json URLs — those were noise bumping
        # the dependency list past anything useful).
        deps = _extract_dependencies(content)

        # Hand the agent_map layer enough state to run custom_roles
        # against the exports. These hidden fields are stripped before
        # the file is written to disk.
        for exp in exports:
            # Trim helper fields that custom_roles uses but JSON
            # consumers don't. We keep `_body` and `_register_call` if
            # the caller wants them, but agent_map removes them after
            # custom-role application.
            pass

        return {"exports": exports, "dependencies": sorted(deps)}

    # ------------------------------------------------------------------
    # Declaration walk
    # ------------------------------------------------------------------

    def _iter_decls(self, text: str):
        """Yield export dicts for every top-level declaration in ``text``.

        Each dict carries: name, kind, params (for funcs), _anchor (the
        offset where the *declaration line* starts — used to scan back
        for JSDoc), _body (the function body slice — used for role
        regexes), _register_call ('').
        """
        # `function NAME(...)` — top-level only thanks to the leading `^`.
        for m in _RE_FUNCTION.finditer(text):
            kind = "async-func" if "async" in (m.group("lead") or "") else "func"
            body, _end = _balance_braces(text, m.end() - 1)
            yield {
                "name": m.group("name"),
                "kind": kind,
                "params": "(" + m.group("params").strip() + ")",
                "_anchor": _line_start(text, m.start()),
                "_body": body,
                "_register_call": "",
            }

        # Anonymous default-export functions get a name of "default" so
        # we still emit *something*; otherwise they'd vanish from the
        # index. Named default exports go through `_RE_FUNCTION` above.
        for m in _RE_DEFAULT_FN.finditer(text):
            name = m.group("name") or "default"
            body, _end = _balance_braces(text, m.end() - 1)
            kind = "async-func" if "async" in text[m.start():m.end()] else "func"
            yield {
                "name": name,
                "kind": kind,
                "params": "(" + m.group("params").strip() + ")",
                "_anchor": _line_start(text, m.start()),
                "_body": body,
                "_register_call": "",
            }

        for m in _RE_CLASS.finditer(text):
            body, _end = _balance_braces(text, m.end() - 1)
            yield {
                "name": m.group("name"),
                "kind": "class",
                "_anchor": _line_start(text, m.start()),
                "_body": body,
                "_register_call": "",
            }

        for m in _RE_CONST_ARROW.finditer(text):
            tail_start = m.end()
            # Body kind: brace-block or expression?
            if tail_start < len(text) and text[tail_start] == "{":
                body, _end = _balance_braces(text, tail_start)
            else:
                body = _take_expression(text, tail_start)
            kind = "async-func" if "async" in text[m.start():m.end()] else "func"
            yield {
                "name": m.group("name"),
                "kind": kind,
                "params": "(" + m.group("params").strip() + ")",
                "_anchor": _line_start(text, m.start()),
                "_body": body,
                "_register_call": "",
            }

        for m in _RE_CONST_FUNCEXPR.finditer(text):
            body, _end = _balance_braces(text, m.end() - 1)
            kind = "async-func" if "async" in text[m.start():m.end()] else "func"
            yield {
                "name": m.group("name"),
                "kind": kind,
                "params": "(" + m.group("params").strip() + ")",
                "_anchor": _line_start(text, m.start()),
                "_body": body,
                "_register_call": "",
            }


# ----------------------------------------------------------------------
# Built-in role detection
# ----------------------------------------------------------------------

def _detect_role(
    exp: dict[str, Any],
    ext: str,
    norm_path: str,
    register_sites,
    full_text: str,
) -> Optional[str]:
    name = exp.get("name") or ""
    kind = exp.get("kind") or ""
    body = exp.get("_body") or ""

    # 1. Express-route — registration site reference wins, regardless
    # of where the handler is declared in the file.
    for m in register_sites:
        handler = m.group("handler")
        if handler == name:
            exp["_register_call"] = m.group(0)
            return "express-route"

    # 2. React component — JSX/TSX + capitalised name + JSX-shaped body.
    if ext in (".jsx", ".tsx") and kind in ("func", "async-func") and name[:1].isupper():
        if _RE_JSX_RETURN.search(body) or _RE_REACT_CE.search(body):
            return "react-component"

    # 3. React hook — any extension; name starts with `use[A-Z]`, body
    # references one of the standard hook builders.
    if kind in ("func", "async-func") and re.match(r'^use[A-Z]', name):
        if _RE_REACT_HOOKS_BODY.search(body):
            return "react-hook"

    # 4. Vue composable — file path under composables/, name starts with
    # `use` (no required body shape — that's the Vue convention).
    if "/composables/" in ("/" + norm_path) and name.startswith("use"):
        return "vue-composable"

    return None


# ----------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------

def _extract_dependencies(text: str) -> set:
    """Return the set of top-level package names imported by ``text``.

    `import x from 'pkg/sub'`  → `pkg`
    `import x from '@scope/pkg/sub'` → `@scope` (mirrors npm scope)
    `import x from './local'`  → dropped (relative paths aren't deps)
    `require('pkg')` → `pkg`
    """
    deps: set = set()
    for source in (
        _RE_IMPORT_FROM.findall(text)
        + _RE_IMPORT_BARE.findall(text)
        + _RE_REQUIRE.findall(text)
    ):
        head = _top_level_pkg(source)
        if head:
            deps.add(head)
    return deps


def _top_level_pkg(spec: str) -> Optional[str]:
    if not spec:
        return None
    spec = spec.strip()
    if spec.startswith(".") or spec.startswith("/"):
        return None
    parts = spec.split("/")
    if not parts:
        return None
    head = parts[0]
    # `@scope/pkg/sub` → keep `@scope`. The Python parser treats the
    # first segment as the package, so we mirror it here.
    return head or None


# ----------------------------------------------------------------------
# Comments / JSDoc handling
# ----------------------------------------------------------------------

def _strip_comments(text: str) -> str:
    """Replace `//` and `/* … */` comments with same-length whitespace.

    Position-invariant — character offsets stay aligned with the
    original source so we can index JSDoc blocks against the original
    text and still match declaration anchors against the scrubbed text.
    Newlines inside block comments survive verbatim so line counts
    don't drift either.
    """
    out: list[str] = list(text)
    i = 0
    n = len(text)
    in_str: Optional[str] = None
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and nxt == "*":
            j = i
            i += 2
            while i < n - 1 and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            if i < n - 1:
                end = i + 2
            else:
                end = n
            for k in range(j, end):
                if text[k] != "\n":
                    out[k] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def _index_jsdoc_blocks(text: str) -> list[tuple[int, int, str]]:
    """Pre-index all `/** ... */` blocks: ``[(start, end, body), ...]``.

    Stored against the *original* (un-stripped) text so we can still
    locate the block immediately preceding a declaration.
    """
    out: list[tuple[int, int, str]] = []
    for m in _RE_JSDOC.finditer(text):
        out.append((m.start(), m.end(), m.group(1)))
    return out


def _jsdoc_for_offset(
    text: str,
    jsdoc_index: list[tuple[int, int, str]],
    decl_offset: int,
) -> Optional[str]:
    """If a JSDoc block ends *immediately above* ``decl_offset`` (only
    whitespace between), return the cleaned first non-empty line.
    """
    candidate: Optional[tuple[int, int, str]] = None
    for start, end, body in jsdoc_index:
        if end > decl_offset:
            break
        candidate = (start, end, body)
    if candidate is None:
        return None
    _, end, body = candidate
    between = text[end:decl_offset]
    if between.strip():
        return None  # something else (code, another comment) sits between
    return _clean_jsdoc(body)


def _clean_jsdoc(body: str) -> Optional[str]:
    """Pull the first meaningful line out of a JSDoc body.

    - Strip leading ``*`` / whitespace per line.
    - Stop the moment we hit an ``@param`` / ``@returns`` / ``@throws``
      block — those describe parameters, not the function purpose.
    - Cap the result at 120 chars.
    """
    for raw in body.splitlines():
        line = raw.strip().lstrip("*").strip()
        if not line:
            continue
        if line.startswith("@"):
            return None
        # Strip trailing tag blocks if the first line contains them.
        return line[:120]
    return None


# ----------------------------------------------------------------------
# Brace / expression scanning
# ----------------------------------------------------------------------

def _balance_braces(text: str, start: int) -> tuple[str, int]:
    """``text[start]`` must be ``{``. Return ``(body, end_index)`` where
    ``body`` is the slice between the opening ``{`` and the matching
    ``}`` (exclusive on both ends), and ``end_index`` points one past
    the closing brace.
    """
    if start >= len(text) or text[start] != "{":
        return "", start
    depth = 0
    in_str: Optional[str] = None
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return text[start + 1:], n


def _take_expression(text: str, start: int) -> str:
    """Read a single arrow-expression body — until end-of-line or
    a top-level ``;`` / ``,`` / EOF, respecting nested parens / strings.
    """
    depth = 0
    in_str: Optional[str] = None
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                break
        elif depth == 0 and ch in (";", "\n"):
            break
        i += 1
    return text[start:i]


def _line_start(text: str, offset: int) -> int:
    """Walk back to the start of the line containing ``offset``."""
    i = offset
    while i > 0 and text[i - 1] != "\n":
        i -= 1
    return i
