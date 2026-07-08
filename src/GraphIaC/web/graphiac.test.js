/* Tests for the DSL core. Run with:  node --test src/GraphIaC/web/
 *
 * The fixture corpus in dsl/fixtures/ is the sync contract between this
 * parser and the Python one: each *.giac source is paired with the exact
 * *.json parse result both implementations must produce.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const registry = require("./registry.js");
const K = require("./graphiac.js");

const FIXTURES = path.join(__dirname, "..", "..", "..", "dsl", "fixtures");

// drop per-run metadata (line numbers, the inferred flag) for round-trip equality
function shape(graph) {
  return {
    nodes: graph.nodes.map(({ g_id, type, fields }) => ({ g_id, type, fields })),
    edges: graph.edges.map(({ type, fields }) => ({ type, fields })),
  };
}

/* ---- the fixture corpus ---- */
for (const f of fs.readdirSync(FIXTURES).filter((f) => f.endsWith(".giac")).sort()) {
  test(`fixture ${f}`, () => {
    const src = fs.readFileSync(path.join(FIXTURES, f), "utf8");
    const expected = JSON.parse(fs.readFileSync(path.join(FIXTURES, f.replace(/\.giac$/, ".json")), "utf8"));
    const res = K.parse(src, registry);

    const exp = expected.errors || [];
    assert.strictEqual(res.errors.length, exp.length, `error count — got ${JSON.stringify(res.errors)}`);
    exp.forEach((e, i) => {
      assert.strictEqual(res.errors[i].line, e.line, `error ${i} line — got ${JSON.stringify(res.errors[i])}`);
      assert.ok(res.errors[i].msg.includes(e.includes), `expected "${e.includes}" in "${res.errors[i].msg}"`);
    });
    if (expected.graph) assert.deepStrictEqual(res.graph, expected.graph);

    // error-free sources must round-trip: parse(desugar(graph)) === graph
    if (exp.length === 0) {
      const again = K.parse(K.desugar(res.graph, registry), registry);
      assert.deepStrictEqual(again.errors, [], "desugared source re-parses cleanly");
      assert.deepStrictEqual(shape(again.graph), shape(res.graph), "desugar round-trip");
    }
  });
}

/* ---- comments and strings ---- */
test("a # inside a string is not a comment", () => {
  const res = K.parse('b : S3Bucket(bucket_name: "a#b") # real comment', registry);
  assert.deepStrictEqual(res.errors, []);
  assert.strictEqual(res.graph.nodes[0].fields.bucket_name, "a#b");
});

/* ---- constants ---- */
test("constants substitute and may reference earlier constants", () => {
  const res = K.parse('a = "x.co"\nb = a\nhz : HostedZone(domain_name: b)', registry);
  assert.deepStrictEqual(res.errors, []);
  assert.strictEqual(res.graph.nodes[0].fields.domain_name, "x.co");
});

test("redefining a constant warns", () => {
  const res = K.parse('a = "x"\na = "y"\nhz : HostedZone(domain_name: a)', registry);
  assert.strictEqual(res.warnings.length, 1);
  assert.ok(res.warnings[0].msg.includes("redefined"));
});

test("a label may not shadow a constant", () => {
  const res = K.parse('hz = "oops"\nhz : HostedZone(domain_name: "x.co")', registry);
  assert.ok(res.errors.some((e) => e.msg.includes("already a constant")));
});

/* ---- attribute references ---- */
test("a ref to an unknown field errors", () => {
  const src = 'cert : ACMCertificate(domain_name: "x.co")\n' +
    'cf : CloudFrontDistribution(domain_name: "x.co", cert_arn: cert.nope)';
  const res = K.parse(src, registry);
  assert.ok(res.errors.some((e) => e.msg.includes('no field "nope"')));
});

test("refsOf reports data dependencies", () => {
  const src = fs.readFileSync(path.join(FIXTURES, "static-site.giac"), "utf8");
  const res = K.parse(src, registry);
  assert.deepStrictEqual(K.refsOf(res.graph), [{ from: "cert", to: "cf", field: "arn" }]);
});

/* ---- name defaulting ---- */
test("a positional argument needs a name field", () => {
  const res = K.parse('hz : HostedZone("x.co")', registry);
  assert.ok(res.errors.some((e) => e.msg.includes("no name field")));
});

/* ---- edges ---- */
test("endpoint fields cannot be set as args", () => {
  const src = 'hz : HostedZone(domain_name: "x.co")\ncert : ACMCertificate(domain_name: "x.co")\n' +
    "cert -> hz : (hz_g_id: hz)";
  const res = K.parse(src, registry);
  assert.ok(res.errors.some((e) => e.msg.includes("set by the arrow")));
});

test("an explicit edge type must match the endpoints", () => {
  const src = 'hz : HostedZone(domain_name: "x.co")\ncert : ACMCertificate(domain_name: "x.co")\n' +
    "cert -> hz : CloudFrontS3OACEdge";
  const res = K.parse(src, registry);
  assert.ok(res.errors.some((e) => e.msg.includes("connects CloudFrontDistribution -> S3Bucket")));
});

test("a duplicate edge warns", () => {
  const src = 'hz : HostedZone(domain_name: "x.co")\ncert : ACMCertificate(domain_name: "x.co")\n' +
    "cert -> hz\nhz -> cert";
  const res = K.parse(src, registry);
  assert.deepStrictEqual(res.errors, []);
  assert.strictEqual(res.warnings.length, 1);
  assert.ok(res.warnings[0].msg.includes("duplicate edge"));
});

test("a self-edge errors", () => {
  const res = K.parse('hz : HostedZone(domain_name: "x.co")\nhz -> hz', registry);
  assert.ok(res.errors.some((e) => e.msg.includes("cannot connect to itself")));
});

/* ---- file() values ---- */
test("file() stays symbolic and needs a quoted path", () => {
  const res = K.parse('fn : CloudFrontFunction(function_code: file("f.js"))', registry);
  assert.deepStrictEqual(res.errors, []);
  assert.deepStrictEqual(res.graph.nodes[0].fields.function_code, { $file: { path: "f.js" } });

  const bad = K.parse("fn : CloudFrontFunction(function_code: file(f.js))", registry);
  assert.ok(bad.errors.some((e) => e.msg.includes("quoted path")));
});

test('"file" is still a usable name when not followed by (', () => {
  const res = K.parse('file = "x.co"\nhz : HostedZone(domain_name: file)', registry);
  assert.deepStrictEqual(res.errors, []);
  assert.strictEqual(res.graph.nodes[0].fields.domain_name, "x.co");
});

/* ---- statements ---- */
test("an unclosed paren errors with the statement's line", () => {
  const res = K.parse('hz : HostedZone(domain_name: "x.co"', registry);
  assert.strictEqual(res.errors[0].line, 1);
  assert.ok(res.errors[0].msg.includes("unclosed"));
});
