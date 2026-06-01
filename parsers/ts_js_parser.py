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
    * ``ng-component``     — Angular ``@Component`` decorator on class.
    * ``ng-service``       — Angular ``@Injectable`` decorator on class.
    * ``ng-module``        — Angular ``@NgModule`` decorator on class.
    * ``ng-pipe``          — Angular ``@Pipe`` decorator on class.
    * ``ng-directive``     — Angular ``@Directive`` decorator on class.
    * ``ng-guard``         — functional guard in a ``*.guard.ts`` file.

Heuristic, regex-driven, zero deps. The JS/TS world is too gnarly for
a stdlib-only true AST pass — we trade precision for scope.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from parsers import _ts_ast
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
    r"^(?P<lead>(?:export\s+(?:default\s+)?)?(?:async\s+)?)"
    r"function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*\((?P<params>[^)]*)\)\s*(?::\s*[^{;]+)?\s*\{",
    re.M,
)

# `export default function NAME?(...) {…}` — name optional, treat the
# default export as `default` if anonymous (skipped from output then).
_RE_DEFAULT_FN = re.compile(
    r"^export\s+default\s+(?:async\s+)?function\s*"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)?"
    r"\s*\((?P<params>[^)]*)\)\s*(?::\s*[^{;]+)?\s*\{",
    re.M,
)

# `class Foo extends Bar { ... }`
_RE_CLASS = re.compile(
    r"^(?P<lead>(?:export\s+(?:default\s+)?)?(?:abstract\s+)?)"
    r"class\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s+extends\s+[^\s{]+)?(?:\s+implements\s+[^{]+)?\s*\{",
    re.M,
)

# `const Foo = (...) => {…}` / `const Foo = async (...) => {…}` /
# `const Foo = (...) => expr` — arrow with brace OR expression body.
# We capture ``params`` and a `start_of_body` marker; when the body
# starts with `{` we balance braces, otherwise we read up to the next
# top-level newline / semicolon.
_RE_CONST_ARROW = re.compile(
    r"^(?P<lead>(?:export\s+)?)"
    r"(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?"
    r"\s*=\s*(?:async\s+)?"
    r"\((?P<params>[^)]*)\)"
    r"(?:\s*:\s*[^=]+?)?"
    r"\s*=>\s*",
    re.M,
)

# `const X = function (...) {…}` — function-expression form.
_RE_CONST_FUNCEXPR = re.compile(
    r"^(?P<lead>(?:export\s+)?)"
    r"(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?"
    r"\s*=\s*(?:async\s+)?function\s*\*?\s*"
    r"\((?P<params>[^)]*)\)\s*\{",
    re.M,
)

# `interface Foo extends Bar { ... }` — TypeScript only. Type params
# (``<T, U>``) and ``extends Base[, Base2]`` are optional. Body is a
# brace block we balance like the function case.
_RE_INTERFACE = re.compile(
    r"^(?P<lead>(?:export\s+(?:default\s+)?)?)"
    r"interface\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*(?:<[^>]*>)?"
    r"(?:\s+extends\s+[^{]+)?\s*\{",
    re.M,
)

# `type Foo = ...` / `export type Foo<T> = ...` — TypeScript type
# aliases. The RHS can be a union / object literal / generic / etc.;
# we read it as an "expression until ; or newline" via ``_take_expression``.
_RE_TYPE_ALIAS = re.compile(
    r"^(?P<lead>(?:export\s+(?:default\s+)?)?)"
    r"type\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*(?:<[^>]*>)?"
    r"\s*=\s*",
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

# TypeScript `enum Name { ... }` and `const enum Name { ... }`
_RE_ENUM = re.compile(
    r"^(?P<lead>(?:export\s+(?:default\s+)?)?(?:const\s+)?)"
    r"enum\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*\{",
    re.M,
)

# `export const X = { ... }` — object-literal form (action sets, config maps, etc.)
# Does NOT conflict with _RE_CONST_ARROW / _RE_CONST_FUNCEXPR — those run first
# and populate `seen`, so object-literal consts only land here when truly new.
_RE_CONST_OBJ = re.compile(
    r"^(?P<lead>(?:export\s+)?)"
    r"(?:const|let)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=\n]+)?"
    r"\s*=\s*\{",
    re.M,
)

# JSDoc — `/** ... */` block, multi-line.
_RE_JSDOC = re.compile(r"/\*\*([\s\S]*?)\*/")

# Express-style registration site:
#   app.get('/path', handler)
#   router.post('/path', m1, handler)
# We capture the verb (lowered) and the trailing bare-name handler if
# present (for the role-tagging cross-reference).
_RE_EXPRESS_REG = re.compile(
    r"\b(?P<base>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\.(?P<verb>get|post|put|patch|delete|options|head|all|use)"
    r"\s*\("
    r'\s*[`\'"][^`\'"]+[`\'"]\s*,'
    r"(?P<between>[^)]*?)"
    r"(?P<handler>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*\)",
)

# Angular @Input / @Output decorators on class members.
_RE_NG_INPUT = re.compile(
    r"@Input\s*(?:\([^)]*\))?\s+(\w+)",
)
_RE_NG_OUTPUT = re.compile(
    r"@Output\s*(?:\([^)]*\))?\s+(\w+)",
)

# Angular @Component / @Injectable decorator metadata (cheap regex on
# decorator-arg block — doesn't try to parse a full TS object literal,
# just pulls the common string fields). Misses dynamic / computed
# values; that's a known limit, callers fall back to grep.
_RE_NG_SELECTOR = re.compile(r"selector\s*:\s*['\"`]([^'\"`]+)['\"`]")
_RE_NG_TEMPLATE_URL = re.compile(r"templateUrl\s*:\s*['\"`]([^'\"`]+)['\"`]")
_RE_NG_STYLE_URL = re.compile(r"styleUrls?\s*:\s*\[\s*['\"`]([^'\"`]+)['\"`]")
_RE_NG_PROVIDED_IN = re.compile(r"providedIn\s*:\s*['\"`]?([A-Za-z_$][A-Za-z0-9_$]*)['\"`]?")
_RE_NG_STANDALONE = re.compile(r"standalone\s*:\s*(true|false)")

# JSX / React.createElement detection inside a function body.
_RE_JSX_RETURN = re.compile(r"<[A-Za-z][A-Za-z0-9]*")
_RE_REACT_CE = re.compile(r"React\.createElement\s*\(")

# React hook usages — any-of these inside body marks a hook.
_RE_REACT_HOOKS_BODY = re.compile(
    r"\b(useState|useEffect|useMemo|useCallback|useRef|useContext)\s*\("
)


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------


class TsJsParser(BaseParser):
    """Top-level extractor for JS / TS / JSX / TSX files."""

    extensions = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")

    def extract(
        self,
        file_path: str,
        *,
        project_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        content = self._read_file(file_path)
        if not content:
            return {"exports": [], "dependencies": []}

        # Strip comments before scanning for declarations / imports.
        # JSDoc is preserved by capturing it BEFORE this strip via the
        # original-source `_jsdoc_index`.
        jsdoc_index = _index_jsdoc_blocks(content)
        register_sites = list(_RE_EXPRESS_REG.finditer(content))
        scrubbed = _strip_comments(content)

        exports: List[Dict[str, Any]] = []
        seen: set = set()

        for entry in self._iter_decls(scrubbed):
            if entry["name"] in seen:
                continue
            seen.add(entry["name"])
            exports.append(entry)

        # Attach JSDoc-derived `doc` field by looking at the source
        # immediately above each declaration's anchor offset. Same
        # anchor doubles as the source for the symbol's 1-indexed
        # `line` field — `_strip_comments` preserves offsets, so
        # `content.count("\n", 0, anchor) + 1` is the line in the
        # original file.
        for exp in exports:
            anchor = exp.pop("_anchor", None)
            if anchor is None:
                continue
            exp["line"] = content.count("\n", 0, anchor) + 1
            end_line = _end_line_for_anchor(scrubbed, anchor)
            if end_line is not None:
                exp["end_line"] = end_line
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

        # Optional TypeScript AST upgrade — replace regex-derived
        # Angular metadata with values pulled from the real compiler
        # AST when the project opts in via conventions.json. No-op
        # when project_root isn't passed (older callers / unit tests
        # constructing the parser directly) or when the project hasn't
        # enabled the feature.  Falls back silently if Node /
        # typescript aren't installed.
        if (
            project_root
            and ext == ".ts"
            and any(e.get("role", "").startswith("ng-") for e in exports)
        ):
            _maybe_upgrade_with_ast(exports, file_path, project_root)

        # Last-resort fallback for ng-component selector / templateUrl /
        # standalone: scan the entire file body without the 2 KB lookback
        # cap the primary regex path uses. Closes the lms-client gap
        # where ``standalone: false`` + a long ``templateUrl`` decorator
        # block pushed ``selector`` outside the window.
        if ext == ".ts":
            _backfill_ng_metadata(exports, content)

        # Imports → top-level package names only (no relative imports,
        # no DOM globals, no /wp-json URLs — those were noise bumping
        # the dependency list past anything useful).
        deps = _extract_dependencies(content)

        # Hand the agent_map layer enough state to run custom_roles
        # against the exports. These hidden fields are stripped before
        # the file is written to disk.
        for _exp in exports:
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
            kind = "async-func" if "async" in text[m.start() : m.end()] else "func"
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
            kind = "async-func" if "async" in text[m.start() : m.end()] else "func"
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
            kind = "async-func" if "async" in text[m.start() : m.end()] else "func"
            yield {
                "name": m.group("name"),
                "kind": kind,
                "params": "(" + m.group("params").strip() + ")",
                "_anchor": _line_start(text, m.start()),
                "_body": body,
                "_register_call": "",
            }

        # TypeScript: ``interface Foo { ... }``. Always emitted —
        # regex-only cost is microseconds per TS file and the closed
        # blind spot (57.9% empty ratio on TS lookups observed in a
        # real lms-client session) is worth far more than the index
        # bytes. Non-TS files don't carry interfaces so nothing fires.
        for m in _RE_INTERFACE.finditer(text):
            body, _end = _balance_braces(text, m.end() - 1)
            yield {
                "name": m.group("name"),
                "kind": "interface",
                "_anchor": _line_start(text, m.start()),
                "_body": body,
                "_register_call": "",
            }

        # TypeScript: ``type Foo = ...`` aliases. RHS read as an
        # expression — terminates at ``;`` / newline / top-level
        # boundary handled by ``_take_expression``.
        for m in _RE_TYPE_ALIAS.finditer(text):
            body = _take_expression(text, m.end())
            yield {
                "name": m.group("name"),
                "kind": "type",
                "_anchor": _line_start(text, m.start()),
                "_body": body,
                "_register_call": "",
            }

        # TypeScript: ``enum Name { ... }`` and ``const enum Name { ... }``.
        # Fixes the 46 % empty ratio on TS enum lookups (e.g. CourseRegistrationActionName).
        for m in _RE_ENUM.finditer(text):
            body, _end = _balance_braces(text, m.end() - 1)
            yield {
                "name": m.group("name"),
                "kind": "enum",
                "_anchor": _line_start(text, m.start()),
                "_body": body,
                "_register_call": "",
            }

        # ``export const X = { ... }`` — object-literal consts (action sets,
        # config maps, DI tokens). Only fires when _RE_CONST_ARROW /
        # _RE_CONST_FUNCEXPR haven't already claimed the name via `seen`.
        for m in _RE_CONST_OBJ.finditer(text):
            body, _end = _balance_braces(text, m.end() - 1)
            yield {
                "name": m.group("name"),
                "kind": "const",
                "_anchor": _line_start(text, m.start()),
                "_body": body,
                "_register_call": "",
            }


# ----------------------------------------------------------------------
# Built-in role detection
# ----------------------------------------------------------------------


def _detect_role(
    exp: Dict[str, Any],
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
    if kind in ("func", "async-func") and re.match(r"^use[A-Z]", name):
        if _RE_REACT_HOOKS_BODY.search(body):
            return "react-hook"

    # 4. Vue composable — file path under composables/, name starts with
    # `use` (no required body shape — that's the Vue convention).
    if "/composables/" in ("/" + norm_path) and name.startswith("use"):
        return "vue-composable"

    # 5. Angular — detect by decorator preceding the class declaration.
    if kind == "class" and ext == ".ts":
        class_pos = full_text.find(f"class {name}")
        if class_pos != -1:
            # Widen the window — Angular components routinely declare
            # 100+ lines of @Component({...}) metadata (selector,
            # templateUrl, styleUrls, providers, imports for standalone
            # mode, etc.). 2 KB covers the common case without
            # noticeable cost; if the decorator is bigger, the parser
            # gracefully returns no metadata for that field.
            preceding = full_text[max(0, class_pos - 2000) : class_pos]
            m = re.search(r"@(Component|Injectable|NgModule|Pipe|Directive)\s*[\(\{]", preceding)
            if m:
                ng_role_map = {
                    "Component": "ng-component",
                    "Injectable": "ng-service",
                    "NgModule": "ng-module",
                    "Pipe": "ng-pipe",
                    "Directive": "ng-directive",
                }
                role = ng_role_map.get(m.group(1))
                # Pull decorator-arg metadata after the matched `@X(`
                # opener. We bound the slice to the decorator block via
                # `class_pos` so we don't bleed into the class body.
                deco_args = preceding[m.end() :]
                if role == "ng-component":
                    inputs = _RE_NG_INPUT.findall(body)
                    outputs = _RE_NG_OUTPUT.findall(body)
                    if inputs:
                        exp["inputs"] = inputs
                    if outputs:
                        exp["outputs"] = outputs
                    sel = _RE_NG_SELECTOR.search(deco_args)
                    if sel:
                        exp["ng_selector"] = sel.group(1)
                    tpl = _RE_NG_TEMPLATE_URL.search(deco_args)
                    if tpl:
                        exp["ng_template_url"] = tpl.group(1)
                    styles = _RE_NG_STYLE_URL.findall(deco_args)
                    if styles:
                        exp["ng_style_urls"] = styles
                    standalone = _RE_NG_STANDALONE.search(deco_args)
                    if standalone:
                        exp["ng_standalone"] = standalone.group(1) == "true"
                elif role == "ng-service":
                    provided = _RE_NG_PROVIDED_IN.search(deco_args)
                    if provided:
                        exp["ng_provided_in"] = provided.group(1)
                elif role == "ng-directive":
                    sel = _RE_NG_SELECTOR.search(deco_args)
                    if sel:
                        exp["ng_selector"] = sel.group(1)
                elif role == "ng-pipe":
                    pname = re.search(r"name\s*:\s*['\"`]([^'\"`]+)['\"`]", deco_args)
                    if pname:
                        exp["ng_pipe_name"] = pname.group(1)
                return role

    # 6. Angular functional guard — *.guard.ts, function/arrow returning
    # Observable<boolean|UrlTree> or Promise<boolean|UrlTree> or boolean.
    if ext == ".ts" and ".guard." in norm_path and kind in ("func", "async-func"):
        return "ng-guard"

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
        _RE_IMPORT_FROM.findall(text) + _RE_IMPORT_BARE.findall(text) + _RE_REQUIRE.findall(text)
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


def _end_line_for_anchor(scrubbed: str, anchor: int) -> Optional[int]:
    """Return the 1-indexed line where the block starting at ``anchor`` ends.

    Uses brace-depth counting on ``scrubbed`` (comments stripped, offsets
    preserved).  For brace-less declarations (``const X = 5;``,
    ``type Foo = Bar;``) returns the line containing the semicolon.
    Returns ``None`` when the block is malformed or unclosed.
    """
    n = len(scrubbed)
    # If a semicolon comes before the first {, it's a brace-less statement.
    brace = scrubbed.find("{", anchor)
    semi = scrubbed.find(";", anchor)
    if brace == -1 or (semi != -1 and semi < brace):
        end = semi if semi != -1 else scrubbed.rfind("\n", 0, n)
        return scrubbed.count("\n", 0, end) + 1 if end >= 0 else None
    depth = 0
    for i in range(brace, n):
        c = scrubbed[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return scrubbed.count("\n", 0, i) + 1
    return None


def _strip_comments(text: str) -> str:
    """Replace `//` and `/* … */` comments with same-length whitespace.

    Position-invariant — character offsets stay aligned with the
    original source so we can index JSDoc blocks against the original
    text and still match declaration anchors against the scrubbed text.
    Newlines inside block comments survive verbatim so line counts
    don't drift either.
    """
    out: List[str] = list(text)
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


def _index_jsdoc_blocks(text: str) -> List[Tuple[int, int, str]]:
    """Pre-index all `/** ... */` blocks: ``[(start, end, body), ...]``.

    Stored against the *original* (un-stripped) text so we can still
    locate the block immediately preceding a declaration.
    """
    out: List[Tuple[int, int, str]] = []
    for m in _RE_JSDOC.finditer(text):
        out.append((m.start(), m.end(), m.group(1)))
    return out


def _jsdoc_for_offset(
    text: str,
    jsdoc_index: List[Tuple[int, int, str]],
    decl_offset: int,
) -> Optional[str]:
    """If a JSDoc block ends *immediately above* ``decl_offset`` (only
    whitespace between), return the cleaned first non-empty line.
    """
    candidate: Optional[Tuple[int, int, str]] = None
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


def _balance_braces(text: str, start: int) -> Tuple[str, int]:
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
                return text[start + 1 : i], i + 1
        i += 1
    return text[start + 1 :], n


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


def _backfill_ng_metadata(
    exports: List[Dict[str, Any]],
    content: str,
) -> None:
    """For ng-component exports missing ``ng_selector``, scan the entire
    file body for the ``@Component(...)`` block immediately above the
    class and pull selector / templateUrl / standalone.

    Handles long decorator blocks (extensive imports + 100+ line
    metadata) that exceed the primary regex path's 2 KB lookback
    window. Idempotent — never overwrites a value the primary path
    already set.
    """
    for exp in exports:
        if exp.get("role") != "ng-component":
            continue
        if exp.get("ng_selector"):
            continue
        name = exp.get("name")
        if not name:
            continue
        cls_re = re.compile(
            r"(?:^|\n)\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?"
            r"class\s+" + re.escape(name) + r"\b"
        )
        m = cls_re.search(content)
        if not m:
            continue
        # Locate the @Component( closest to the class start (the last
        # @Component before it). No window cap.
        prefix = content[: m.start()]
        candidates = list(re.finditer(r"@Component\s*\(", prefix))
        if not candidates:
            continue
        deco_args = prefix[candidates[-1].end() :]
        sel = _RE_NG_SELECTOR.search(deco_args)
        if sel:
            exp["ng_selector"] = sel.group(1)
        if not exp.get("ng_template_url"):
            tpl = _RE_NG_TEMPLATE_URL.search(deco_args)
            if tpl:
                exp["ng_template_url"] = tpl.group(1)
        if exp.get("ng_standalone") is None:
            standalone = _RE_NG_STANDALONE.search(deco_args)
            if standalone:
                exp["ng_standalone"] = standalone.group(1) == "true"


# ----------------------------------------------------------------------
# Optional AST upgrade (Feature Q)
# ----------------------------------------------------------------------


def _maybe_upgrade_with_ast(
    exports: List[Dict[str, Any]],
    file_path: str,
    project_root: str,
) -> None:
    """Replace regex-based Angular metadata with AST-derived values
    when the project opts into ``typescript_ast`` and Node + the
    ``typescript`` package are reachable.

    The AST extractor knows how to follow ``providedIn: SomeConst``,
    decorator factory wrappers, and other shapes the regex misses.
    On any failure (Node missing, typescript missing, parse error)
    this is a no-op and the regex-derived fields stay.
    """
    if not _ts_ast.is_enabled(project_root):
        return
    records = _ts_ast.parse(file_path, project_root)
    if not records:
        return
    by_name = {r.get("name"): r for r in records if isinstance(r, dict) and r.get("name")}
    for exp in exports:
        rec = by_name.get(exp.get("name"))
        if rec is None:
            continue
        # Role override is intentional — the AST is authoritative when
        # available.  This catches @Injectable() inside a NgModule's
        # providers array etc., which the regex sometimes misses.
        role = rec.get("role")
        if role:
            exp["role"] = role
        if rec.get("selector") is not None:
            exp["ng_selector"] = rec["selector"]
        if rec.get("templateUrl") is not None:
            exp["ng_template_url"] = rec["templateUrl"]
        if rec.get("styleUrls"):
            exp["ng_style_urls"] = rec["styleUrls"]
        if rec.get("standalone") is not None:
            exp["ng_standalone"] = rec["standalone"]
        if rec.get("providedIn") is not None:
            exp["ng_provided_in"] = rec["providedIn"]
        if rec.get("pipeName") is not None:
            exp["ng_pipe_name"] = rec["pipeName"]
        if rec.get("inputs"):
            exp["inputs"] = rec["inputs"]
        if rec.get("outputs"):
            exp["outputs"] = rec["outputs"]
