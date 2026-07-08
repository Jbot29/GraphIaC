# GraphIaC

> **Early Alpha — Experimental Software**
> GraphIaC is in early alpha. APIs will change, features are incomplete, and it has not been tested in production. Use at your own risk.

A graph-based Infrastructure-as-Code framework for AWS. Model your cloud infrastructure as a directed graph — **nodes are AWS resources, edges are the connections and permissions between them** — in a small declarative language, with a live browser UI to edit, plan, and apply it.

## The Problem

Tools like Terraform and Pulumi make it easy to define individual resources. The hard part is wiring them together: IAM policies, invoke permissions, service integrations. That boilerplate is repetitive, error-prone, and the main source of permission debugging in real projects. Most IaC tools treat it as an afterthought, burying it inside resource definitions in ways that make the code hard to read and impossible to reuse.

## The Approach

GraphIaC promotes connections to first-class citizens. When you write `cf -> bucket`, the edge already knows it means an Origin Access Control bucket policy — it queries the graph for the relevant ARNs and provisions everything itself. You declare the connection; the edge handles the boilerplate.

Because AWS permission patterns are stable, that knowledge gets written once into the edge class and reused everywhere. And because the infrastructure is a graph, the diagram you see in the UI is never out of date — it *is* the code.

## Quick Start

**1. Install (or upgrade):**

```bash
pip install --upgrade GraphIaC
```

**2. Have an AWS profile ready.** GraphIaC uses your local AWS credentials (`~/.aws/credentials`), selected by profile name. The profile needs permissions for whatever you plan to manage (for early testing, an admin-ish sandbox account is the practical choice).

**3. Write an infra file.** Infrastructure is described in a small language (files end in `.giac`). A complete static website:

```
# site.giac — Route53 -> CloudFront (HTTPS) -> S3
domain = "example.com"

hz     : HostedZone(domain_name: domain)
cert   : ACMCertificate(domain_name: domain)
bucket : S3Bucket("example-com-site")
cf     : CloudFrontDistribution(domain_name: domain, cert_arn: cert.arn)

cert -> hz          # DNS validation records, automatically
cf   -> bucket      # OAC: only this distribution can read the bucket
cf   -> hz : (domain_name: domain)   # A alias record
```

**4. Start the server and open the UI:**

```bash
python -m GraphIaC <your-profile> --infra_file site.giac serve
# GraphIaC serving site.giac at http://127.0.0.1:8642
```

Open **http://127.0.0.1:8642**. You get:

- **Editor (left)** — your `.giac` source, re-parsed as you type; errors and warnings appear inline with line numbers. **💾 save** writes it back to the file on disk.
- **Diagram (right)** — the live graph. Solid arrows are provisioned connections (each one is an IAM policy, bucket policy, DNS record, or integration you didn't have to write). Dashed teal arrows are data dependencies.
- **▷ plan** — diffs your source against the state DB and live AWS, and shows the result both as a log (`+` create, `~` update, `-` delete, `↳` import, `⊘` blocked) and as badges on the diagram.
- **▶ run** — applies the plan to AWS (asks for confirmation first).
- **✓ verify** — an independent audit: reads live AWS state and runs per-resource security/config checks.
- **⇄ desugar** — shows your source with every shorthand resolved: constants substituted, inferred edge types written out, defaults made explicit.

The SQLite state DB is created next to the infra file (`site.giac` → `site.db`).

The server binds `127.0.0.1` only and has **no authentication** — don't expose the port.

### Things to know before pointing it at a real account

- **`plan` and `verify` are read-only.** `run` is what changes AWS.
- **Removing a node from the source schedules its deletion.** Once a resource is tracked in the state DB, deleting its line means the next plan shows `- will be deleted` and the next run deletes it from AWS. Check the plan before running.
- Resources that already exist in AWS are **imported, not recreated** — see below.

## The Language in Sixty Seconds

Five ideas, nothing more (full spec: [`dsl/spec.md`](dsl/spec.md)):

```
name = "value"              # a constant, substituted at parse time
label : Type(field: value)  # a node — the label IS its identity, and
                            #   defaults into the type's name field
a -> b                      # an edge — its type is INFERRED from the
                            #   node-type pair; `: Type(args)` overrides
other.field                 # an attribute reference — a data dependency
                            #   resolved from live AWS state at plan time
# comment
```

**Name defaulting:** `my-site-bucket : S3Bucket` needs nothing else — the label names the bucket. One rule replaces the id/name split: the label names the thing everywhere, unless you say otherwise.

**Edge inference:** every edge is uniquely determined by its endpoints — `cf -> bucket` can only mean the OAC edge, `cert -> hz` only DNS validation. You point; the edge knows. Arrow direction doesn't matter; the parser normalizes it.

**Slow resources & BLOCKED:** some resources take hours to become usable (ACM certificate validation is the classic). There is no phase logic in the source. A reference like `cert.arn` resolves from live AWS state; until the certificate is ISSUED, everything depending on it is reported **`⊘ BLOCKED`** (greyed out in the diagram) and simply skipped. Run again later and the planner picks up where AWS left off.

## Headless CLI (no UI)

Every UI action works directly on a `.giac` file — useful for scripts and CI:

```bash
python -m GraphIaC <profile> --infra_file site.giac plan     # preview changes
python -m GraphIaC <profile> --infra_file site.giac run      # apply
python -m GraphIaC <profile> --infra_file site.giac verify   # audit; exits 1 on failure
python -m GraphIaC <profile> --infra_file site.giac diagram  # Graphviz PNG (needs pygraphviz)
```

## Importing Existing Resources

If you built infrastructure by hand (or with another tool) before adopting GraphIaC, you don't tear it down. Import is **automatic**: when a node is declared in your source and exists in AWS but isn't in the state DB yet, plan marks it `↳ import` — the next run records its live state without touching AWS.

```
# adopt a site that already exists
bucket : S3Bucket("my-site-prod", region: "us-east-2")
cf     : CloudFrontDistribution(domain_name: "example.com",
                                cert_arn: "arn:aws:acm:us-east-1:...:certificate/...")
```

Worth knowing:

- **Declare only the fields you care about.** Drift is checked only on fields you explicitly set, so a sparse declaration won't false-positive against the fully-populated AWS resource.
- **Lookup is by natural key.** Most nodes can be found by bucket name, domain, or zone name — you usually don't need to hunt down ARNs or IDs first.
- **Edges are re-asserted, not imported.** They're idempotent: on the first run they re-apply against the existing wiring (a no-op if it's already in place) and are tracked from then on.

## What's in the Box

Current node types: S3, CloudFront (distributions + functions), Route53, ACM, IAM roles, Lambda, DynamoDB, API Gateway (HTTP APIs), SES, and the edges that wire them together — DNS validation, OAC bucket policies, alias records, execution-role policies, route integrations + invoke permissions, and more. The full inference table is in [`dsl/spec.md`](dsl/spec.md); the sandbox's error messages will tell you when no edge exists between two types yet.

## Python API

GraphIaC is DSL-first, but the underlying Python interface is still available — every node and edge is a Pydantic model you can compose in code (`GraphIaC.init`, `add_node`, `add_edge`, `plan`, `run`, `verify`), and `--infra_file infra.py` files exposing an `infra(state)` function still work with the CLI. Expect the DSL path to be the one that gets the attention going forward.

## Running Tests

```bash
pytest tests/test_dsl.py tests/test_dsl_load.py tests/test_server.py   # engine + DSL (no AWS; uses moto)
node --test src/GraphIaC/web/                                          # the JS parser twin
AWS_PROFILE=your-profile pytest tests/aws/                             # real-AWS integration tests
```

The two DSL parsers (JavaScript for the editor, Python for the engine) are kept in sync by a shared fixture corpus in `dsl/fixtures/` — both suites run it.

AWS integration tests generate randomized resource names, clean up after themselves (including on failure), and default to `us-east-2`. Without `AWS_PROFILE` set they're skipped automatically.

## License

MIT — see [LICENSE](LICENSE).
