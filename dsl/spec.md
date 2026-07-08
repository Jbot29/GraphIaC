# GraphIaC DSL — language spec v0.1

A small, code-first language for describing cloud infrastructure as **nodes
connected by intelligent edges** — and then **provisioning** it. The same
source is the definition, the plan, and the diagram. Designed to be legible to
a human at a glance and to a language model in full.

Its one governing idea, inherited from GraphIaC itself: **intelligence lives
in the edge, not in your config.** When you write `cf -> bucket`, the edge
already knows the bucket policy, the OAC wiring, and the IAM boilerplate that
connection requires. You declare the relationship; the edge provisions it.
The DSL makes that thesis visible in the syntax: the arrow is the language's
centerpiece.

This is a working draft. Scope is deliberately tiny: labels, constants, nodes,
arrows, and attribute references — enough to state every current example, and
no more. Anything else is deferred until a real script forces it.

## How it relates to GraphIaC today

The DSL is **purely a frontend**. A source file parses down to the exact flat
structure the engine already consumes: a list of nodes and edges (the same
Pydantic models registered in `model_map.py`). `plan`, `run`, and `verify`
never see the sugar. A Python `infra.py` and a DSL file are two doors into the
same room, and both remain supported.

---

## Comments

`#` begins a comment; everything after it on the line is ignored.

```
# a full-line comment
bucket : S3Bucket("my-site")   # a trailing comment
```

---

## Constants

A bare `name = value` binds a constant. Constants are parse-time only — they
never reach the engine; every use is substituted before the graph is built.

```
domain = "begrif.co"

hz : HostedZone(domain_name: domain)
```

Values are strings (`"…"`), numbers, booleans (`true` / `false`), lists
(`[a, b]`), or maps (`{name: "id", attr_type: "S"}`).

---

## Nodes — the label is the identity

A **node** is one AWS resource: a label, a type, and its fields.

```
<label> : <TypeName>(<args>)
```

- The **label is the `g_id`**. There is no separate identifier; the name you
  gave the thing in the source is the name it has in the graph, the DB, and
  the diagram. Labels are letters, digits, `_`, and `-`.
- `TypeName` must be a registered node type (see *The registry*).
- Args are `field: value` pairs matching the type's Pydantic fields. The
  parens are optional when there are no args.

```
hz     : HostedZone(domain_name: "begrif.co")
api    : ApiSite
bucket : S3Bucket(bucket_name: "begrif-co-site", region: "us-east-2")
```

### Name defaulting — the label names the thing everywhere

Most node types have a *name field* — the human-chosen AWS name (`bucket_name`,
`table_name`, `site_name`, `name`, …). If the name field is not given, **it
defaults to the label**:

```
begrif-co-site : S3Bucket        # bucket_name = "begrif-co-site"
users : DynamoTable(partition_key: {name: "id", attr_type: "S"})
                                 # table_name = "users"
```

One rule replaces today's g_id/AWS-name split: *the label names the thing
everywhere, unless you say otherwise.* Fields that are identifying but not
name-like (`domain_name`, `runtime`, `handler`, …) never default from the
label and must be written out.

### One positional argument — the name field

A single leading positional argument binds to the type's name field, for the
common case where the AWS name is the only thing worth saying:

```
bucket : S3Bucket("begrif-co-site")
```

All other args are named.

---

## Edges — the arrow

An **edge** is a relationship between two nodes, and the reason this language
exists.

```
<label> -> <label> [ : <EdgeType>(<args>) | : (<args>) ]
```

### Inference — you point, the edge knows

The edge type is **inferred from the pair of node types**. Every edge in
GraphIaC today is uniquely determined by its endpoints — a
`CloudFrontDistribution -> S3Bucket` arrow can only mean the OAC edge — so
the common case is just the arrow:

```
cert -> hz          # ACMCertificateHostedZoneEdge: DNS validation records
cf   -> bucket      # CloudFrontS3OACEdge: locked-down bucket policy
role -> fn          # IAMRolePolicyLambdaEdge: basic execution policy
```

Arrow order does not affect inference — the pair is looked up unordered, and
the parser normalizes to the edge's canonical direction (the one its class
defines). Desugared output always shows the canonical direction.

### Explicit types and args — the `:` channel

When an edge needs arguments, or (in the future) a pair has more than one
possible edge, a `:` clause follows the arrow — the same definition channel
nodes use:

```
cf -> hz : (domain_name: domain)                       # inferred type, args given
cf -> hz : CloudFrontRoute53Edge(domain_name: domain)  # fully explicit
```

An edge with more than two participants names the extras as args; the arrow
carries the primary pair:

```
fn -> ses : (role_g_id: role)      # LambdaSESEdge also touches the role
```

A bare node label used as a value (like `role` above) resolves to that node's
`g_id`.

### Current inference table

| source type            | destination type       | edge                          |
|------------------------|------------------------|-------------------------------|
| `ACMCertificate`       | `HostedZone`           | `ACMCertificateHostedZoneEdge`|
| `CloudFrontDistribution` | `S3Bucket`           | `CloudFrontS3OACEdge`         |
| `CloudFrontDistribution` | `HostedZone`         | `CloudFrontRoute53Edge`       |
| `CloudFrontFunction`   | `CloudFrontDistribution` | `CloudFrontFunctionEdge`    |
| `ApiSite`              | `ApiEndpoint`          | `SiteEndpointEdge`            |
| `ApiEndpoint`          | `LambdaZipFile`        | `EndpointLambdaEdge`          |
| `IAMRole`              | `LambdaZipFile`        | `IAMRolePolicyLambdaEdge`     |
| `SESDomainIdentity`    | `HostedZone`           | `SESDomainRoute53Edge`        |
| `LambdaZipFile`        | `SESDomainIdentity`    | `LambdaSESEdge`               |

This table is not hand-maintained in two places — it is generated from the
registry (below).

---

## Attribute references — data flows along the graph

A field value may reference another node's live attribute:

```
cf : CloudFrontDistribution(domain_name: domain, cert_arn: cert.arn)
```

`cert.arn` is not substituted at parse time — the planner resolves it from
live/DB state when the plan is built. An attribute reference is a **data
dependency**: it declares that `cf` cannot be planned until `cert` exists and
the referenced field has a value.

This is what replaces the imperative two-phase pattern (`read()` the cert,
`if status != "ISSUED": return`) from Python infra files. The whole graph is
always declared; readiness is the planner's problem, not the author's.

---

## Files — code rides along

Some fields *are* code — a CloudFront function's JavaScript, a policy
document. The DSL has no multiline strings; code lives in its own file,
next to the source, and is referenced:

```
fn : CloudFrontFunction(name: "url-rewrite", function_code: file("url-rewrite.js"))
```

`file(...)` takes one quoted path, relative to the `.giac` source file.
Parsing never touches the disk — the value stays symbolic (`$file`) in the
graph, so the browser sandbox works without filesystem access; the
**engine** reads the file at load time. A missing file is an authoring
error (load fails), not a BLOCKED.

`file` is only special immediately before `(` — it remains usable as a
label or constant name.

## BLOCKED — the planner handles time

Some resources take hours to become usable (ACM certificate validation is the
canonical case). The DSL has **no control flow** for this. Instead:

- A node class may define a readiness check (e.g. `ACMCertificate` is ready
  only when `status == "ISSUED"`).
- At plan time, any node whose attribute references cannot be resolved — or
  whose upstream dependency exists but is not ready — is marked **`BLOCKED`**
  instead of `CREATE`, along with everything downstream of it.
- `run` executes everything unblocked and skips the rest. Run again later and
  the planner picks up where AWS left off — blocked resources unblock
  automatically as their upstreams become ready.

The plan (and the UI's diagram) shows the *entire* intended graph, with the
blocked subgraph greyed out — better than today, where the not-yet-ready half
of the graph silently doesn't exist.

`BLOCKED` is the one place the engine changes to support the DSL: a new plan
operation alongside `CREATE | UPDATE | DELETE | IMPORT`.

---

## Desugar — watching the edge dissolve

`desugar(graph)` re-emits any parsed source with every lens resolved:
constants substituted, inferred edge types written out, name fields and
defaults made explicit, arrows in canonical direction. The output is itself
valid DSL and **re-parses to the identical graph** (the tests assert this
round-trip).

One level deeper, the UI can expand an edge into **the provisioning it
performs** — the bucket policy JSON behind `cf -> bucket`, the IAM policy
behind `role -> fn`. That view is the project's thesis made executable: you
declare a relationship, and you can watch it dissolve into the boilerplate it
saved you from writing.

---

## The graph object (for tools, parsers, and models)

`parse(src)` returns `{ graph, errors, warnings }`, each error/warning
carrying a `line`. The graph is plain JSON — the contract between the two
parser implementations and the engine:

```
graph = {
  nodes: [ { g_id, type, fields: { … }, line } ],
  edges: [ { type, fields: { …_g_id, … }, inferred, line } ],
}
```

An unresolved attribute reference is serialized as
`{ "$ref": { "g_id": "cert", "field": "arn" } }` in place of a value.

## The registry — one source of truth for two parsers

There are two parser implementations: **JavaScript** (the live editor —
instant feedback, no AWS) and **Python** (the engine — the truth at
`plan`/`run` time). The grammar is small enough that maintaining both is
cheap, and two things keep them honest:

1. **`registry.js`** — generated by `python -m GraphIaC.dsl_registry` from the
   Pydantic models and `model_map.py`: every node type's fields, defaults, and
   name field; every edge type's endpoint types, endpoint field names, and
   canonical direction. It is a UMD module (loadable from a plain
   `<script src>` with no build step, and `require()`-able in Node). The JS
   parser consumes it and never hand-codes AWS knowledge.
2. **A shared fixture corpus** — `dsl/fixtures/*.giac` sources paired with
   expected `*.json` graph output (and expected errors for invalid sources).
   Both test suites run the same corpus; a fixture passing in one parser and
   failing in the other is a sync bug by definition.

Source files use the `.giac` extension.

---

## Worked examples

### A bucket — the smallest graph

One node, no edges. The label is the g_id *and* the bucket name.

```
my-test-bucket : S3Bucket(region: "us-east-2")
```

### Static site — Route53 -> CloudFront (HTTPS) -> S3

The full stack from `examples/static-site/infra.py`, including its two-phase
ACM dance — with no phases in the source. On the first run `cert` and `hz`
provision and everything below `cf` is `BLOCKED` on `cert.arn` / the cert
reaching `ISSUED`. Run again after validation and the rest comes up.

```
domain = "begrif.co"

hz     : HostedZone(domain_name: domain)
cert   : ACMCertificate(domain_name: domain)
bucket : S3Bucket("begrif-co-site")
cf     : CloudFrontDistribution(domain_name: domain, cert_arn: cert.arn)

cert -> hz                        # validation CNAMEs, automatically
cf   -> bucket                    # OAC: only this distribution can read
cf   -> hz : (domain_name: domain)  # A alias record
```

Nine lines. The Python version is ~50, and half of it is the phase logic the
planner now owns.

### An API — Gateway -> Lambda, with a role

```
api-role : IAMRole
handler  : LambdaZipFile(runtime: "python3.12", handler: "app.handler",
                         zip_file_path: "build/app.zip")
api      : ApiSite(region: "us-east-2")
hello    : ApiEndpoint(method: "GET", path: "/hello")

api-role -> handler       # execution policy attached
api      -> hello         # route on the HTTP API
hello    -> handler       # integration + invoke permission
```

Every `->` here is an IAM policy or a service integration someone would
otherwise write by hand.

---

## Not yet in the language (deferred until an example forces it)

- **Guard blocks** (`when cert.status == "ISSUED" { … }`) — declarative
  conditionals. Deferred to see whether `BLOCKED` + attribute references
  cover every real case; so far they do.
- **Arrow chaining** (`api -> hello -> handler`) — sugar for consecutive
  edges. Cheap, but nothing forces it yet.
- **String interpolation** (`"${domain}-site"`) — until a script repeats
  itself painfully without it.
- **A global `region` lens** — one `region : us-east-2` statement that fills
  every node's unset region, the way klangbild's `tempo` resolves beats.
  Complicated by per-service defaults (SES and ACM want `us-east-1`).
- **Custom node types** — the DSL covers types registered in
  `model_map.py`; Python `infra.py` remains the escape hatch.
- **Modules / imports, counts / loops, outputs** — each deferred until a
  real script demands it.
