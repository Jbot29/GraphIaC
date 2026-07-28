/* =====================================================================
 * GraphIaC DSL v0.1 — language core (pure: parser + desugar)
 * No DOM, no AWS. Runs in the browser (window.GraphIaCDSL) and in Node
 * (module.exports), so the same code powers the sandbox and the tests.
 *
 * Design thesis (see dsl/spec.md): intelligence lives in the EDGE. The
 * language has five ideas and no more:
 *
 *   label : Type(args)     a node — the label IS the g_id, and defaults
 *                          into the type's name field
 *   a -> b                 an edge — its type INFERRED from the node-type
 *                          pair; `: Type(args)` makes it explicit
 *   name = value           a constant, substituted at parse time
 *   other.field            an attribute reference — a data dependency the
 *                          planner resolves from live state ($ref)
 *   #                      a comment
 *
 * Everything resolves at parse time to a flat graph — plain JSON nodes and
 * edges, exactly what the Python engine consumes. All AWS knowledge (types,
 * fields, defaults, the edge inference table) comes from registry.js, which
 * is GENERATED from the Pydantic models: this file knows no AWS.
 * ===================================================================== */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.GraphIaCDSL = api;
})(typeof self !== "undefined" ? self : this, function () {
"use strict";

const VERSION = "0.1";
const IDENT = /^[A-Za-z_][A-Za-z0-9_-]*$/;          // labels may contain dashes
const TYPE_RE = /^([A-Za-z_][A-Za-z0-9_]*)?\s*(\(([\s\S]*)\))?$/; // Type, Type(...), or (...)
const DEFINE_RE = /^define\s+([A-Za-z_][A-Za-z0-9_-]*)\s*\(([\s\S]*?)\)\s*\{([\s\S]*)\}$/;
// like TYPE_RE but dashes allowed: module names follow label style, AWS
// type names never contain one
const HEAD_RE = /^([A-Za-z_][A-Za-z0-9_-]*)?\s*(\(([\s\S]*)\))?$/;

/* ---------------------------------------------------------------------
 * Lines -> statements. A '#' outside a string starts a comment; a
 * statement continues across lines while ( [ { stay open, so multi-line
 * arg lists just work.
 * ------------------------------------------------------------------- */
function stripComment(line) {
  let inStr = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inStr) { if (c === "\\") i++; else if (c === '"') inStr = false; }
    else if (c === '"') inStr = true;
    else if (c === "#") return line.slice(0, i);
  }
  return line;
}

// bracket-depth change of a comment-stripped line, ignoring strings
function depthDelta(text) {
  let d = 0, inStr = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inStr) { if (c === "\\") i++; else if (c === '"') inStr = false; }
    else if (c === '"') inStr = true;
    else if (c === "(" || c === "[" || c === "{") d++;
    else if (c === ")" || c === "]" || c === "}") d--;
  }
  return d;
}

function toStatements(src) {
  const out = [];
  let buf = null, startLn = 0, depth = 0;
  String(src).split("\n").forEach((raw, i) => {
    const text = stripComment(raw);
    if (buf === null) {
      if (text.trim() === "") return;
      buf = text; startLn = i + 1; depth = depthDelta(text);
    } else {
      buf += "\n" + text; depth += depthDelta(text);
    }
    if (depth <= 0) { out.push({ ln: startLn, text: buf.trim() }); buf = null; depth = 0; }
  });
  if (buf !== null) out.push({ ln: startLn, text: buf.trim(), unclosed: true });
  return out;
}

// index of `tok` outside strings and outside any brackets, or -1
function indexTopLevel(text, tok) {
  let d = 0, inStr = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inStr) { if (c === "\\") i++; else if (c === '"') inStr = false; }
    else if (c === '"') inStr = true;
    else if (c === "(" || c === "[" || c === "{") d++;
    else if (c === ")" || c === "]" || c === "}") d--;
    else if (d === 0 && text.startsWith(tok, i)) return i;
  }
  return -1;
}

function badInterpName(raw) {
  // the obvious thing to try, and it can't work: interpolation happens at
  // parse time, and an attribute reference has no value until plan
  if (raw.includes(".")) {
    return `attribute references cannot be interpolated — write ${raw} as the whole value, not inside a string`;
  }
  return `bad name "${raw}" in \${...}`;
}

// A constant rendered into a string, or null if it has no sensible
// rendering (interpolating a list into a name is a mistake, not a
// formatting question).
function interpStr(val) {
  if (typeof val === "boolean") return val ? "true" : "false";
  if (typeof val === "number") return String(val);
  if (typeof val === "string") return val;
  return null;
}

/* ---------------------------------------------------------------------
 * Value scanner — strings, numbers, booleans, lists, maps, identifiers,
 * and dotted attribute references (other.field). Returns TAGGED values;
 * resolve() below turns them into the plain JSON the graph carries.
 * ------------------------------------------------------------------- */
function makeScanner(s, ln, err) {
  let i = 0;
  const ws = () => { while (i < s.length && /\s/.test(s[i])) i++; };
  const eof = () => { ws(); return i >= s.length; };
  const rest = () => s.slice(i);

  function ident() {
    ws();
    const m = s.slice(i).match(/^[A-Za-z_][A-Za-z0-9_-]*/);
    if (!m) return null;
    i += m[0].length;
    return m[0];
  }

  // A string literal, with `${constant}` interpolation. A string containing
  // no `${` produces the same plain "str" token it always did; only an
  // interpolated one becomes "interp", whose parts are literal chunks and
  // {name} placeholders resolved against the constants at parse time.
  // `\${` escapes an interpolation.
  function string() {
    i++; // opening quote
    const parts = [];
    let buf = "";
    while (i < s.length) {
      const c = s[i];
      if (c === "\\" && i + 1 < s.length) { buf += s[i + 1]; i += 2; continue; }
      if (c === '"') {
        i++;
        if (!parts.length) return { t: "str", v: buf };
        if (buf) parts.push(buf);
        return { t: "interp", parts };
      }
      if (c === "$" && s[i + 1] === "{") {
        const close = s.indexOf("}", i + 2);
        if (close < 0) { err(ln, "unterminated ${ in string"); return null; }
        const name = s.slice(i + 2, close).trim();
        if (!/^[A-Za-z_][A-Za-z0-9_-]*$/.test(name)) { err(ln, badInterpName(name)); return null; }
        if (buf) { parts.push(buf); buf = ""; }
        parts.push({ name });
        i = close + 1;
        continue;
      }
      buf += c; i++;
    }
    err(ln, "unterminated string");
    return null;
  }

  function value() {
    ws();
    if (i >= s.length) { err(ln, "expected a value"); return null; }
    const c = s[i];
    if (c === '"') return string();
    if (c === "[") {
      i++; const v = [];
      ws();
      if (s[i] === "]") { i++; return { t: "list", v }; }
      for (;;) {
        const e = value();
        if (!e) return null;
        v.push(e); ws();
        if (s[i] === ",") { i++; continue; }
        if (s[i] === "]") { i++; return { t: "list", v }; }
        err(ln, "expected , or ] in list"); return null;
      }
    }
    if (c === "{") {
      i++; const v = {};
      ws();
      if (s[i] === "}") { i++; return { t: "map", v }; }
      for (;;) {
        ws();
        let k = null;
        if (s[i] === '"') { const ks = string(); if (!ks) return null; k = ks.v; }
        else { k = ident(); }
        if (!k) { err(ln, "expected a key in map"); return null; }
        ws();
        if (s[i] !== ":") { err(ln, `expected : after map key "${k}"`); return null; }
        i++;
        const e = value();
        if (!e) return null;
        v[k] = e; ws();
        if (s[i] === ",") { i++; continue; }
        if (s[i] === "}") { i++; return { t: "map", v }; }
        err(ln, "expected , or } in map"); return null;
      }
    }
    const num = s.slice(i).match(/^[+-]?(\d+\.?\d*|\.\d+)/);
    if (num) { i += num[0].length; return { t: "num", v: parseFloat(num[0]) }; }
    const id = ident();
    if (id) {
      if (id === "true" || id === "false") return { t: "bool", v: id === "true" };
      if (id === "file" && s[i] === "(") {
        i++;
        ws();
        if (s[i] !== '"') { err(ln, 'file(...) takes a quoted path — e.g. file("handler.js")'); return null; }
        const p = string();
        if (!p) return null;
        ws();
        if (s[i] !== ")") { err(ln, "expected ) after file path"); return null; }
        i++;
        return { t: "fileval", path: p.v };
      }
      if (s[i] === ".") {
        i++;
        const f = ident();
        if (!f) { err(ln, `expected a field name after "${id}."`); return null; }
        return { t: "ref", g_id: id, field: f };
      }
      return { t: "ident", v: id };
    }
    err(ln, `bad value at "${clip(rest())}"`);
    return null;
  }

  return { ws, eof, rest, ident, value, at: () => s[i], advance: () => i++, mark: () => i, seek: (p) => { i = p; } };
}

// `(args)` body -> { positional, named } of tagged values
// `define f(a, b: 8000)` parameter list -> [{name, default}]. Not parseArgs:
// there a bare identifier is the one positional argument; here it's a
// required parameter, and there can be many.
function parseParams(inner, ln, err) {
  const params = [];
  if (inner === null || inner === undefined || inner.trim() === "") return params;
  const sc = makeScanner(inner, ln, err);
  for (;;) {
    if (sc.eof()) break;
    const pname = sc.ident();
    if (!pname) { err(ln, `expected a parameter name at "${clip(sc.rest())}"`); return params; }
    let def = null;
    sc.ws();
    if (sc.at() === ":") {
      sc.advance();
      def = sc.value();
      if (!def) return params;
    }
    params.push({ name: pname, default: def });
    sc.ws();
    if (sc.eof()) break;
    if (sc.at() === ",") { sc.advance(); continue; }
    err(ln, `expected , between parameters — at "${clip(sc.rest())}"`);
    return params;
  }
  return params;
}

// [label, typeName, argsRaw] for a `label : Type(args)` statement, or null.
// Used to spot module instantiations before node pass 1, since an instance
// is written exactly like a node.
function stmtHead(text) {
  const ci = indexTopLevel(text, ":");
  if (ci < 0) return null;
  const label = text.slice(0, ci).trim();
  const m = text.slice(ci + 1).trim().match(HEAD_RE);
  if (!m || !m[1]) return null;
  return [label, m[1], m[3] || ""];
}

// A label as written inside a module body -> the label it means. Body
// labels are prefixed with the instance label; a parameter holding a node
// label (`hz: hz` at the call site) resolves to that node. Anything else is
// left alone so the caller's "unknown node" error fires.
function localLabel(scope, name) {
  if (!scope) return name;
  if (Object.prototype.hasOwnProperty.call(scope.labels, name)) return scope.labels[name];
  const v = scope.consts.get(name);
  if (typeof v === "string") return v;
  return name;
}

function parseArgs(inner, ln, err) {
  const named = {}; let positional = null;
  if (inner == null || inner.trim() === "") return { positional, named };
  const sc = makeScanner(inner, ln, err);
  let first = true;
  for (;;) {
    if (sc.eof()) break;
    const save = sc.mark();
    const id = sc.ident();
    sc.ws();
    let v = null, name = null;
    if (id && sc.at() === ":") {
      sc.advance();
      name = id;
      v = sc.value();
    } else {
      sc.seek(save);
      v = sc.value();
    }
    if (!v) return { positional, named };
    if (name) {
      if (name in named) err(ln, `argument "${name}" given twice`);
      named[name] = v;
    } else if (first) {
      positional = v;
    } else {
      err(ln, "only one leading positional argument is allowed — name the rest");
    }
    first = false;
    sc.ws();
    if (sc.eof()) break;
    if (sc.at() === ",") { sc.advance(); continue; }
    err(ln, `expected , between arguments — at "${clip(sc.rest())}"`);
    return { positional, named };
  }
  return { positional, named };
}

/* ---------------------------------------------------------------------
 * PARSE — source -> { graph: { nodes, edges }, errors, warnings }.
 * Declaration order is free (klangbild-style): constants first, then all
 * node labels are collected, THEN fields resolve — so a node may reference
 * a node defined below it.
 * ------------------------------------------------------------------- */
function parse(src, registry) {
  const errors = [], warnings = [];
  const err = (ln, msg) => errors.push({ line: ln, msg });
  const warn = (ln, msg) => warnings.push({ line: ln, msg });
  const graph = { nodes: [], edges: [], guards: [] };
  if (!registry || !registry.nodes || !registry.edges) {
    err(0, "no type registry given — load registry.js and pass it to parse(src, registry)");
    return { graph, errors, warnings };
  }

  // ---- classify statements ----
  const constStmts = [], edgeStmts = [], guardStmts = [], defineStmts = [];
  let nodeStmts = [];
  for (const st of toStatements(src)) {
    if (st.unclosed) { err(st.ln, "unclosed ( [ or { — statement never ends"); continue; }
    if (/^define\b/.test(st.text)) defineStmts.push(st);
    else if (st.text.startsWith("?")) guardStmts.push(st);
    else if (indexTopLevel(st.text, "->") >= 0) edgeStmts.push(st);
    else if (indexTopLevel(st.text, "=") >= 0) constStmts.push(st);
    else if (indexTopLevel(st.text, ":") >= 0) nodeStmts.push(st);
    else err(st.ln, `unrecognized statement (expected name = value, label : Type, a -> b, ? predicate(...), or define name(...) {…}): "${clip(st.text)}"`);
  }

  // ---- module definitions ----
  //
  // A module is a macro over the graph, not a type: `define` collects
  // statements, an instance replays them with its parameters bound and its
  // labels prefixed, and by the time there is a graph no module exists.
  // That is what keeps modules composable where a construct isn't — the
  // expansion is ordinary DSL, and desugar needs to know nothing.
  const modules = new Map();
  for (const st of defineStmts) {
    const m = st.text.match(DEFINE_RE);
    if (!m) { err(st.ln, "bad define — expected: define name(param, param: default) { … }"); continue; }
    const modName = m[1], paramsRaw = m[2], bodyRaw = m[3];
    if (registry.nodes[modName] || registry.edges[modName]) {
      err(st.ln, `"${modName}" is already an AWS type — pick another module name`); continue;
    }
    if (modules.has(modName)) {
      err(st.ln, `module "${modName}" defined twice (first on line ${modules.get(modName).line})`); continue;
    }

    const params = parseParams(paramsRaw, st.ln, err);
    if (params.some((x) => x.name === "name")) {
      err(st.ln, '"name" is reserved inside a body — it holds the instance label'); continue;
    }

    // body lines are numbered from the source, not from the block
    const brace = st.text.indexOf("{");
    const bodyOffset = st.ln + (st.text.slice(0, brace).match(/\n/g) || []).length;
    const body = { nodes: [], edges: [], guards: [] };
    for (const raw of toStatements(bodyRaw)) {
      const bln = bodyOffset + raw.ln - 1;
      const bst = { ln: bln, text: raw.text };
      if (raw.unclosed) err(bln, "unclosed ( [ or { — statement never ends");
      else if (bst.text.startsWith("?")) body.guards.push(bst);
      else if (/^define\b/.test(bst.text)) err(bln, "a module cannot define another module");
      else if (indexTopLevel(bst.text, "->") >= 0) body.edges.push(bst);
      else if (indexTopLevel(bst.text, "=") >= 0) err(bln, "constants are not allowed in a module body — use a parameter with a default");
      else if (indexTopLevel(bst.text, ":") >= 0) body.nodes.push(bst);
      else err(bln, `unrecognized statement in module "${modName}": "${clip(bst.text)}"`);
    }

    const bodyLabels = new Set();
    for (const b of body.nodes) { const h = stmtHead(b.text); if (h) bodyLabels.add(h[0]); }
    const clash = params.map((x) => x.name).filter((n) => bodyLabels.has(n)).sort();
    if (clash.length) {
      err(st.ln, `parameter "${clash[0]}" has the same name as a node in the body`); continue;
    }
    modules.set(modName, { name: modName, params, body, line: st.ln });
  }

  // ---- constants (parse-time only; may use earlier constants) ----
  const consts = new Map();
  for (const st of constStmts) {
    const eq = indexTopLevel(st.text, "=");
    const name = st.text.slice(0, eq).trim();
    if (!IDENT.test(name)) { err(st.ln, `bad constant name "${clip(name)}"`); continue; }
    const sc = makeScanner(st.text.slice(eq + 1), st.ln, err);
    const v = sc.value();
    if (!v) continue;
    if (!sc.eof()) { err(st.ln, `unexpected text after constant value: "${clip(sc.rest())}"`); continue; }
    const plain = resolve(v, st.ln, { consts, nodes: null, refsAllowed: false });
    if (plain === undefined) continue;
    if (consts.has(name)) warn(st.ln, `constant "${name}" redefined`);
    consts.set(name, plain);
  }

  // ---- module instances: replay each body with its parameters bound ----
  //
  // An instance is written exactly like a node (`web : web-service(...)`),
  // so instances are separated out here, before pass 1 would call the
  // module name an unknown node type.
  const instances = [];
  let realNodeStmts = [];
  for (const st of nodeStmts) {
    const head = stmtHead(st.text);
    if (head && modules.has(head[1])) {
      instances.push({ st, label: head[0], module: modules.get(head[1]), argsRaw: head[2] });
    } else realNodeStmts.push(st);
  }
  nodeStmts = realNodeStmts;

  // Every label the file will end up with, known from syntax alone —
  // needed before arguments resolve, because an argument may name a node
  // ( `hz: hz` ) and nothing has been collected yet.
  const instanceOf = new Map();
  const allLabels = new Map();
  for (const st of nodeStmts) { const h = stmtHead(st.text); if (h) allLabels.set(h[0], { type: h[1] }); }
  for (const inst of instances) {
    inst.labels = {};
    for (const bst of inst.module.body.nodes) {
      const h = stmtHead(bst.text);
      if (!h) continue;
      const expanded = `${inst.label}-${h[0]}`;
      inst.labels[h[0]] = expanded;
      allLabels.set(expanded, { type: h[1] });
      instanceOf.set(expanded, inst.label);
    }
  }

  for (const inst of instances) {
    const st = inst.st, mod = inst.module;
    const { positional, named } = parseArgs(inst.argsRaw, st.ln, err);
    if (positional !== null) err(st.ln, `${mod.name} is a module — its arguments must all be named`);

    const scopeConsts = new Map([["name", inst.label]]);
    const known = new Set(mod.params.map((x) => x.name));
    for (const f of Object.keys(named)) {
      if (!known.has(f)) err(st.ln, `${mod.name} has no parameter "${f}"`);
    }
    for (const prm of mod.params) {
      if (Object.prototype.hasOwnProperty.call(named, prm.name)) {
        // a ref may ride in as an argument: it stays symbolic all the way
        // to the field it lands on, and the planner blocks that node
        // exactly as it would have without the module
        const r = resolve(named[prm.name], st.ln, { consts, nodes: allLabels, refsAllowed: true });
        if (r !== undefined) scopeConsts.set(prm.name, r);
      } else if (prm.default !== null) {
        const r = resolve(prm.default, mod.line, { consts, nodes: allLabels, refsAllowed: true });
        if (r !== undefined) scopeConsts.set(prm.name, r);
      } else {
        err(st.ln, `${mod.name} needs an argument for "${prm.name}"`);
      }
    }

    const scope = { prefix: inst.label, consts: scopeConsts, labels: inst.labels };
    const merged = new Map([...consts, ...scopeConsts]);
    for (const [kind, target] of [["nodes", nodeStmts], ["edges", edgeStmts], ["guards", guardStmts]]) {
      for (const bst of mod.body[kind]) {
        target.push({ ln: bst.ln, text: bst.text, scope, consts: merged });
      }
    }
  }

  const constsFor = (st) => st.consts || consts;

  function unknownNodeMsg(lbl) {
    if (consts.has(lbl)) return `"${lbl}" is a constant, not a node`;
    if (modules.has(lbl) || instances.some((i) => i.label === lbl)) {
      const inner = [...instanceOf.entries()].filter(([, o]) => o === lbl).map(([k]) => k).sort();
      return `"${lbl}" is a module instance, not a node` + (inner.length ? ` — did you mean ${inner[0]}?` : "");
    }
    return `unknown node "${lbl}" in edge`;
  }

  // ---- nodes, pass 1: collect every label and type ----
  const nodes = new Map(); // label -> node record
  for (const st of nodeStmts) {
    const ci = indexTopLevel(st.text, ":");
    let label = st.text.slice(0, ci).trim();
    const restStr = st.text.slice(ci + 1).trim();
    if (!IDENT.test(label)) { err(st.ln, `bad label "${clip(label)}"`); continue; }
    if (st.scope && Object.prototype.hasOwnProperty.call(st.scope.labels, label)) label = st.scope.labels[label];
    if (consts.has(label)) { err(st.ln, `"${label}" is already a constant — labels and constants share one namespace`); continue; }
    if (nodes.has(label)) { err(st.ln, `duplicate label "${label}" (first defined on line ${nodes.get(label).line})`); continue; }
    const m = restStr.match(TYPE_RE);
    if (!m || !m[1]) { err(st.ln, `expected a node type after "${label} :"`); continue; }
    const type = m[1];
    if (!registry.nodes[type]) {
      if (registry.edges[type]) err(st.ln, `"${type}" is an edge type — edges are written a -> b`);
      else err(st.ln, `unknown node type "${type}"`);
      continue;
    }
    nodes.set(label, { g_id: label, type, fields: {}, line: st.ln, argsRaw: m[3] || "",
                       consts: constsFor(st), scope: st.scope || null });
  }

  // a tagged value -> the plain JSON the graph carries
  function resolve(v, ln, env) {
    switch (v.t) {
      case "str": case "num": case "bool": return v.v;
      case "list": {
        const out = [];
        for (const e of v.v) { const r = resolve(e, ln, env); if (r === undefined) return undefined; out.push(r); }
        return out;
      }
      case "map": {
        const out = {};
        for (const [k, e] of Object.entries(v.v)) { const r = resolve(e, ln, env); if (r === undefined) return undefined; out[k] = r; }
        return out;
      }
      case "interp": {
        let out = "";
        for (const p of v.parts) {
          if (typeof p === "string") { out += p; continue; }
          if (!env.consts.has(p.name)) {
            err(ln, `unknown name "${p.name}" in \${...} — only constants can be interpolated`);
            return undefined;
          }
          const rendered = interpStr(env.consts.get(p.name));
          if (rendered === null) {
            err(ln, `cannot interpolate "${p.name}" — only strings, numbers, and booleans`);
            return undefined;
          }
          out += rendered;
        }
        return out;
      }
      case "fileval":
        // stays symbolic — the ENGINE reads the file at load time, relative
        // to the source file; the browser only needs the reference
        return { $file: { path: v.path } };
      case "ident": {
        if (env.consts.has(v.v)) return env.consts.get(v.v);
        const lbl = localLabel(env.scope, v.v);
        if (env.nodes && env.nodes.has(lbl)) return lbl;   // a bare label means its g_id
        err(ln, `unknown name "${v.v}" — not a constant${env.nodes ? " or node label" : ""}`);
        return undefined;
      }
      case "ref": {
        if (!env.refsAllowed) { err(ln, `attribute references (${v.g_id}.${v.field}) are not allowed here`); return undefined; }
        const gId = localLabel(env.scope, v.g_id);
        const target = env.nodes.get(gId);
        if (!target) { err(ln, `reference to unknown node "${v.g_id}" in ${v.g_id}.${v.field}`); return undefined; }
        // during the argument phase the label table holds types only, and an
        // unregistered type is pass 1's error to report, not this one's
        const reg = registry.nodes[target.type];
        if (!reg) return { $ref: { g_id: gId, field: v.field } };
        if (!(v.field in reg.fields)) { err(ln, `${target.type} has no field "${v.field}" (in ${v.g_id}.${v.field})`); return undefined; }
        return { $ref: { g_id: gId, field: v.field } };
      }
    }
    return undefined;
  }

  // ---- nodes, pass 2: resolve fields, default the name, check required ----
  for (const node of nodes.values()) {
    const reg = registry.nodes[node.type];
    const { positional, named } = parseArgs(node.argsRaw, node.line, err);
    const env = { consts: node.consts, nodes, refsAllowed: true, scope: node.scope };
    const fields = {};
    if (positional) {
      if (!reg.nameField) err(node.line, `${node.type} has no name field — a positional argument means nothing here; name every field`);
      else {
        const r = resolve(positional, node.line, env);
        if (r !== undefined) fields[reg.nameField] = r;
      }
    }
    for (const [f, v] of Object.entries(named)) {
      if (!(f in reg.fields)) { err(node.line, `${node.type} has no field "${f}"`); continue; }
      if (f in fields) { err(node.line, `field "${f}" already set by the positional argument`); continue; }
      const r = resolve(v, node.line, env);
      if (r !== undefined) fields[f] = r;
    }
    if (reg.nameField && !(reg.nameField in fields)) fields[reg.nameField] = node.g_id;  // the label names the thing
    for (const [f, info] of Object.entries(reg.fields)) {
      if (info.required && !(f in fields)) err(node.line, `${node.type} "${node.g_id}" is missing required field "${f}"`);
    }
    node.fields = fields;
    graph.nodes.push({ g_id: node.g_id, type: node.type, fields: node.fields, line: node.line });
  }

  // a subclass node matches its ancestors' edge endpoints and guard
  // targets (registry "isa" chain — DeployRole rides IAMRole's edges,
  // S3BucketKMS will ride S3Bucket's)
  function isA(actual, want) {
    while (actual != null) {
      if (actual === want) return true;
      actual = (registry.nodes[actual] || {}).isa;
    }
    return false;
  }

  // ---- edges ----
  const seenEdges = new Set();
  for (const st of edgeStmts) {
    const ai = indexTopLevel(st.text, "->");
    let aLabel = st.text.slice(0, ai).trim();
    let rhs = st.text.slice(ai + 2).trim();
    if (indexTopLevel(rhs, "->") >= 0) { err(st.ln, "one arrow per statement"); continue; }
    const ci = indexTopLevel(rhs, ":");
    let bLabel = (ci < 0 ? rhs : rhs.slice(0, ci)).trim();
    const clause = ci < 0 ? null : rhs.slice(ci + 1).trim();

    aLabel = localLabel(st.scope, aLabel);
    bLabel = localLabel(st.scope, bLabel);

    let ok = true;
    for (const lbl of [aLabel, bLabel]) {
      if (!nodes.has(lbl)) { err(st.ln, unknownNodeMsg(lbl)); ok = false; }
    }
    if (!ok) continue;
    if (aLabel === bLabel) { err(st.ln, `a node cannot connect to itself ("${aLabel}")`); continue; }

    // optional `: EdgeType(args)` / `: EdgeType` / `: (args)`
    let explicitType = null, argsRaw = "";
    if (clause !== null) {
      const m = clause.match(TYPE_RE);
      if (!m || (!m[1] && !m[2])) { err(st.ln, `bad edge clause ": ${clip(clause)}"`); continue; }
      explicitType = m[1] || null;
      argsRaw = m[3] || "";
    }

    // the pair of node types picks the edge; arrow order is normalized
    const ta = nodes.get(aLabel).type, tb = nodes.get(bLabel).type;
    let type = null, srcLabel = aLabel, dstLabel = bLabel;
    if (explicitType) {
      const reg = registry.edges[explicitType];
      if (!reg) { err(st.ln, `unknown edge type "${explicitType}"`); continue; }
      if (isA(ta, reg.source.type) && isA(tb, reg.dest.type)) { /* as written */ }
      else if (isA(tb, reg.source.type) && isA(ta, reg.dest.type)) { srcLabel = bLabel; dstLabel = aLabel; }
      else { err(st.ln, `${explicitType} connects ${reg.source.type} -> ${reg.dest.type}, not ${ta} -> ${tb}`); continue; }
      type = explicitType;
    } else {
      const matches = [];
      for (const [name, reg] of Object.entries(registry.edges)) {
        if (isA(ta, reg.source.type) && isA(tb, reg.dest.type)) matches.push({ name, flip: false });
        else if (isA(tb, reg.source.type) && isA(ta, reg.dest.type)) matches.push({ name, flip: true });
      }
      if (!matches.length) { err(st.ln, `no edge type known between ${ta} and ${tb} — see the inference table in dsl/spec.md`); continue; }
      if (matches.length > 1) { err(st.ln, `ambiguous edge between ${ta} and ${tb} (${matches.map(m => m.name).join(", ")}) — write the type explicitly`); continue; }
      type = matches[0].name;
      if (matches[0].flip) { srcLabel = bLabel; dstLabel = aLabel; }
    }

    const reg = registry.edges[type];
    const fields = {};
    fields[reg.source.field] = srcLabel;
    fields[reg.dest.field] = dstLabel;

    const { positional, named } = parseArgs(argsRaw, st.ln, err);
    const env = { consts: constsFor(st), nodes, refsAllowed: true, scope: st.scope };
    if (positional) err(st.ln, "edge arguments must be named");
    for (const [f, v] of Object.entries(named)) {
      if (!(f in reg.fields)) { err(st.ln, `${type} has no field "${f}"`); continue; }
      if (f === reg.source.field || f === reg.dest.field) { err(st.ln, `"${f}" is set by the arrow itself`); continue; }
      const r = resolve(v, st.ln, env);
      if (r !== undefined) fields[f] = r;
    }
    for (const [f, info] of Object.entries(reg.fields)) {
      if (info.required && !(f in fields)) err(st.ln, `${type} ${srcLabel} -> ${dstLabel} is missing required field "${f}"`);
    }

    const key = `${type}|${srcLabel}|${dstLabel}`;
    if (seenEdges.has(key)) warn(st.ln, `duplicate edge ${srcLabel} -> ${dstLabel} (${type})`);
    seenEdges.add(key);
    graph.edges.push({ type, fields, inferred: !explicitType, line: st.ln });
  }

  // ---- guards: ? predicate(label, ...) ----
  const predicates = registry.predicates || {};
  const guardRe = /^\?\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\(([^)]*)\)\s*$/;
  for (const st of guardStmts) {
    const m = st.text.match(guardRe);
    if (!m) { err(st.ln, "? needs a predicate — e.g. ? private(bucket)"); continue; }
    const [, name, argStr] = m;
    const spec = predicates[name];
    if (!spec) { err(st.ln, `unknown predicate "${name}"`); continue; }
    const args = (argStr.trim() ? argStr.split(",").map((a) => a.trim()) : [])
      .map((a) => localLabel(st.scope, a));
    const expected = spec.args;
    if (args.length !== expected.length) {
      err(st.ln, `${name} takes ${expected.length} argument${expected.length !== 1 ? "s" : ""} (${expected.join(", ")}), got ${args.length}`);
      continue;
    }
    let ok = true;
    args.forEach((label, i) => {
      if (!nodes.has(label)) { err(st.ln, `unknown node "${label}" in guard`); ok = false; }
      else if (!isA(nodes.get(label).type, expected[i])) {
        err(st.ln, `${name} expects ${expected[i]} for argument ${i + 1}, got ${nodes.get(label).type} ("${label}")`);
        ok = false;
      }
    });
    if (ok) graph.guards.push({ predicate: name, args, line: st.ln });
  }

  return { graph, errors, warnings };
}

/* ---------------------------------------------------------------------
 * DESUGAR — re-emit a parsed graph as source with every lens resolved:
 * constants substituted, name fields explicit, every edge type written
 * out, arrows in canonical direction. The output is valid DSL and
 * re-parses to the identical graph (the tests assert this round-trip).
 * ------------------------------------------------------------------- */
function fmtValue(v) {
  if (v === null) return "null";
  // ${ is escaped so desugar output re-parses to this same string rather
  // than to an interpolation of it
  if (typeof v === "string") return JSON.stringify(v).replace(/\$\{/g, "\\${");
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return "[" + v.map(fmtValue).join(", ") + "]";
  if (typeof v === "object" && v.$ref) return `${v.$ref.g_id}.${v.$ref.field}`;
  if (typeof v === "object" && v.$file) return `file(${JSON.stringify(v.$file.path)})`;
  if (typeof v === "object") return "{" + Object.entries(v).map(([k, e]) => `${k}: ${fmtValue(e)}`).join(", ") + "}";
  return String(v);
}
function fmtFields(fields, skip) {
  const parts = [];
  for (const [f, v] of Object.entries(fields)) {
    if (skip && skip.has(f)) continue;
    parts.push(`${f}: ${fmtValue(v)}`);
  }
  return parts.join(", ");
}

function desugar(graph, registry) {
  const out = [
    "# desugared — every lens resolved:",
    "#   constants substituted · labels -> names · inferred edges explicit · arrows canonical",
    "",
  ];
  for (const n of graph.nodes) {
    const args = fmtFields(n.fields);
    out.push(`${n.g_id} : ${n.type}${args ? `(${args})` : ""}`);
  }
  if (graph.edges.length) out.push("");
  for (const e of graph.edges) {
    const reg = registry.edges[e.type];
    const src = e.fields[reg.source.field], dst = e.fields[reg.dest.field];
    const extras = fmtFields(e.fields, new Set([reg.source.field, reg.dest.field]));
    out.push(`${src} -> ${dst} : ${e.type}${extras ? `(${extras})` : ""}`);
  }
  if ((graph.guards || []).length) out.push("");
  for (const g of graph.guards || []) {
    out.push(`? ${g.predicate}(${g.args.join(", ")})`);
  }
  return out.join("\n");
}

/* ---------------------------------------------------------------------
 * refsOf — every attribute reference in a graph, as data-dependency
 * triples for tools (the sandbox draws these as dashed arrows).
 * ------------------------------------------------------------------- */
function refsOf(graph) {
  const refs = [];
  const walk = (v, add) => {
    if (v && typeof v === "object") {
      if (v.$ref) add(v.$ref);
      else for (const e of Object.values(v)) walk(e, add);
    }
  };
  for (const n of graph.nodes) walk(n.fields, (r) => refs.push({ from: r.g_id, to: n.g_id, field: r.field }));
  for (const e of graph.edges) walk(e.fields, (r) => refs.push({ from: r.g_id, to: null, field: r.field, edge: e }));
  return refs;
}

function clip(s) { s = String(s).trim(); return s.length > 40 ? s.slice(0, 40) + "…" : s; }

return { VERSION, parse, desugar, refsOf, stripComment };
});
