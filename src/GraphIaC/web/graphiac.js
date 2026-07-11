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

  function string() {
    i++; // opening quote
    let v = "";
    while (i < s.length) {
      const c = s[i];
      if (c === "\\" && i + 1 < s.length) { v += s[i + 1]; i += 2; continue; }
      if (c === '"') { i++; return { t: "str", v }; }
      v += c; i++;
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
  const constStmts = [], nodeStmts = [], edgeStmts = [], guardStmts = [];
  for (const st of toStatements(src)) {
    if (st.unclosed) { err(st.ln, "unclosed ( [ or { — statement never ends"); continue; }
    if (st.text.startsWith("?")) guardStmts.push(st);
    else if (indexTopLevel(st.text, "->") >= 0) edgeStmts.push(st);
    else if (indexTopLevel(st.text, "=") >= 0) constStmts.push(st);
    else if (indexTopLevel(st.text, ":") >= 0) nodeStmts.push(st);
    else err(st.ln, `unrecognized statement (expected name = value, label : Type, a -> b, or ? predicate(...)): "${clip(st.text)}"`);
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

  // ---- nodes, pass 1: collect every label and type ----
  const nodes = new Map(); // label -> node record
  for (const st of nodeStmts) {
    const ci = indexTopLevel(st.text, ":");
    const label = st.text.slice(0, ci).trim();
    const restStr = st.text.slice(ci + 1).trim();
    if (!IDENT.test(label)) { err(st.ln, `bad label "${clip(label)}"`); continue; }
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
    nodes.set(label, { g_id: label, type, fields: {}, line: st.ln, argsRaw: m[3] || "" });
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
      case "fileval":
        // stays symbolic — the ENGINE reads the file at load time, relative
        // to the source file; the browser only needs the reference
        return { $file: { path: v.path } };
      case "ident": {
        if (env.consts.has(v.v)) return env.consts.get(v.v);
        if (env.nodes && env.nodes.has(v.v)) return v.v;   // a bare label means its g_id
        err(ln, `unknown name "${v.v}" — not a constant${env.nodes ? " or node label" : ""}`);
        return undefined;
      }
      case "ref": {
        if (!env.refsAllowed) { err(ln, `attribute references (${v.g_id}.${v.field}) are not allowed here`); return undefined; }
        const target = env.nodes.get(v.g_id);
        if (!target) { err(ln, `reference to unknown node "${v.g_id}" in ${v.g_id}.${v.field}`); return undefined; }
        const reg = registry.nodes[target.type];
        if (!(v.field in reg.fields)) { err(ln, `${target.type} has no field "${v.field}" (in ${v.g_id}.${v.field})`); return undefined; }
        return { $ref: { g_id: v.g_id, field: v.field } };
      }
    }
    return undefined;
  }
  const env = { consts, nodes, refsAllowed: true };

  // ---- nodes, pass 2: resolve fields, default the name, check required ----
  for (const node of nodes.values()) {
    const reg = registry.nodes[node.type];
    const { positional, named } = parseArgs(node.argsRaw, node.line, err);
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
    const aLabel = st.text.slice(0, ai).trim();
    let rhs = st.text.slice(ai + 2).trim();
    if (indexTopLevel(rhs, "->") >= 0) { err(st.ln, "one arrow per statement"); continue; }
    const ci = indexTopLevel(rhs, ":");
    const bLabel = (ci < 0 ? rhs : rhs.slice(0, ci)).trim();
    const clause = ci < 0 ? null : rhs.slice(ci + 1).trim();

    let ok = true;
    for (const lbl of [aLabel, bLabel]) {
      if (!nodes.has(lbl)) {
        err(st.ln, consts.has(lbl) ? `"${lbl}" is a constant, not a node` : `unknown node "${lbl}" in edge`);
        ok = false;
      }
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
    const args = argStr.trim() ? argStr.split(",").map((a) => a.trim()) : [];
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
  if (typeof v === "string") return JSON.stringify(v);
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
