#!/usr/bin/env node
// TypeScript AST extractor for Angular decorator metadata.
//
// Invoked by parsers/_ts_ast.py.  Receives a file path on argv[2] and
// emits one JSON record per exported Angular class on stdout:
//
//     {"name": "CartService", "role": "ng-service",
//      "selector": null, "templateUrl": null, "styleUrls": [],
//      "providedIn": "root", "standalone": null,
//      "inputs": ["item"], "outputs": ["removed"]}
//
// Failures (no typescript installed, file unreadable, etc.) print a
// JSON error object to stdout and exit non-zero.  The Python wrapper
// treats any non-zero exit as "AST unavailable, fall back to regex".
//
// Designed to be loaded via the parent project's typescript install
// (./node_modules/typescript) — vc-context itself ships zero npm
// dependencies.  Falls back to the global typescript install if the
// local one isn't present.

import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

const argv = process.argv.slice(2);
if (argv.length < 1) {
  console.log(JSON.stringify({error: 'usage: extractor.mjs <ts-file> [project-root]'}));
  process.exit(2);
}
const filePath = argv[0];
const projectRoot = argv[1] || process.cwd();

let ts;
try {
  // Prefer the project's local typescript so version mismatches with a
  // global install don't bite (modules-resolution rules differ across
  // major versions).
  const localRequire = createRequire(path.join(projectRoot, 'package.json'));
  ts = localRequire('typescript');
} catch (e1) {
  try {
    ts = (await import('typescript')).default;
  } catch (e2) {
    console.log(JSON.stringify({
      error: 'typescript not installed (tried local + global)',
      detail: e2.message,
    }));
    process.exit(3);
  }
}

let source;
try {
  source = readFileSync(filePath, 'utf8');
} catch (e) {
  console.log(JSON.stringify({error: 'read failed', detail: e.message}));
  process.exit(4);
}

const sf = ts.createSourceFile(
  filePath, source, ts.ScriptTarget.Latest, /*setParentNodes*/ true
);

const NG_ROLE = {
  Component: 'ng-component',
  Injectable: 'ng-service',
  NgModule: 'ng-module',
  Pipe: 'ng-pipe',
  Directive: 'ng-directive',
};

function literalText(node) {
  if (!node) return null;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
    return node.text;
  }
  // Identifier ⇒ surface the name (e.g. providedIn: SOME_CONST). Caller
  // can resolve later if it cares; we just record the symbol.
  if (ts.isIdentifier(node)) return node.text;
  if (node.kind === ts.SyntaxKind.TrueKeyword) return true;
  if (node.kind === ts.SyntaxKind.FalseKeyword) return false;
  return null;
}

function arrayOfStrings(node) {
  if (!node || !ts.isArrayLiteralExpression(node)) return null;
  return node.elements.map(literalText).filter(v => typeof v === 'string');
}

function decoratorMeta(decorator) {
  // Decorator is `@Foo(arg)` — argument is at decorator.expression.arguments[0]
  const expr = decorator.expression;
  if (!ts.isCallExpression(expr)) return null;
  const callee = expr.expression;
  if (!ts.isIdentifier(callee)) return null;
  const role = NG_ROLE[callee.text];
  if (!role) return null;
  const props = {};
  const arg = expr.arguments[0];
  if (arg && ts.isObjectLiteralExpression(arg)) {
    for (const p of arg.properties) {
      if (!ts.isPropertyAssignment(p)) continue;
      const key = p.name && (ts.isIdentifier(p.name) || ts.isStringLiteral(p.name)) ? p.name.text : null;
      if (!key) continue;
      if (key === 'styleUrls') {
        const arr = arrayOfStrings(p.initializer);
        if (arr) props.styleUrls = arr;
      } else {
        const v = literalText(p.initializer);
        if (v !== null) props[key] = v;
      }
    }
  }
  return {role, props};
}

function memberDecoratorNames(member) {
  if (!ts.canHaveDecorators(member)) return [];
  const decos = ts.getDecorators(member) || [];
  const names = [];
  for (const d of decos) {
    const e = d.expression;
    const callee = ts.isCallExpression(e) ? e.expression : e;
    if (ts.isIdentifier(callee)) names.push(callee.text);
  }
  return names;
}

const records = [];
function visit(node) {
  if (ts.isClassDeclaration(node) && node.name) {
    const className = node.name.text;
    const decos = (ts.canHaveDecorators(node) ? ts.getDecorators(node) : null) || [];
    for (const d of decos) {
      const meta = decoratorMeta(d);
      if (!meta) continue;
      const inputs = [];
      const outputs = [];
      for (const m of node.members || []) {
        const mname = m.name && (ts.isIdentifier(m.name) || ts.isStringLiteral(m.name)) ? m.name.text : null;
        if (!mname) continue;
        const dnames = memberDecoratorNames(m);
        if (dnames.includes('Input'))  inputs.push(mname);
        if (dnames.includes('Output')) outputs.push(mname);
      }
      records.push({
        name: className,
        role: meta.role,
        selector: meta.props.selector ?? null,
        templateUrl: meta.props.templateUrl ?? null,
        styleUrls: meta.props.styleUrls ?? [],
        standalone: typeof meta.props.standalone === 'boolean' ? meta.props.standalone : null,
        providedIn: meta.props.providedIn ?? null,
        pipeName: meta.role === 'ng-pipe' ? (meta.props.name ?? null) : null,
        inputs,
        outputs,
      });
      break;  // one Angular decorator per class is the convention.
    }
  }
  ts.forEachChild(node, visit);
}
visit(sf);
process.stdout.write(JSON.stringify(records));
