# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

GraphIac is a graph-based Infrastructure-as-Code framework for AWS. The core idea: model cloud infrastructure as a directed graph where **nodes are AWS resources** and **edges are the connections and permissions between them**.

The problem it solves: tools like Terraform/Pulumi make defining individual resources easy, but wiring them together — IAM policies, invoke permissions, service integrations — produces repetitive boilerplate JSON that's painful to write and maintain. The same permission patterns (Lambda→DynamoDB, Lambda→SSM, API Gateway→Lambda) appear over and over across every project.

**The key design insight: intelligence lives in the edge, not the user's config.** When you add a `LambdaToDynamoEdge`, the edge implementation already knows what IAM policies are required. It queries the graph to discover the relevant role ARN, table name, etc., and provisions everything itself. The user just declares the connection; the edge handles the boilerplate. Because AWS permission patterns are stable, this "baked-in intelligence" doesn't need to change — write it once in the edge class, reuse it everywhere.

## Development Workflow

All changes should be made on a feature branch and submitted as a pull request — never commit directly to `main`.

## Commands

**Install for development:**
```bash
pip install -e ".[dev]"
```

**Build:**
```bash
python3 -m build
# or: ./build.sh
```

**Run tests:**
```bash
pytest tests/
pytest tests/test_graph.py          # unit tests only
pytest tests/aws/test_dynamodb.py   # requires AWS credentials
```

**Freeze dependencies:**
```bash
./freeze.sh  # pip freeze > requirements.txt
```

**Publish to PyPI:**
```bash
./publish.sh
```
Auto-bumps the patch version in `pyproject.toml`, builds, and uploads. User must run this themselves — it requires their PyPI API key. Package is live at `pip install GraphIaC`.

## Architecture

### Core Engine (`src/GraphIaC/main.py`)

Six public functions form the user-facing API: `init()`, `add_node()`, `add_edge()`, `plan()`, `run()`, `verify()`.

- `init(session, db_conn)` → `GraphIaCState`: creates a NetworkX DiGraph and connects to the SQLite state DB
- `plan(state)`: reads current AWS state for every node/edge via boto3, diffs against the local DB, returns a list of `(operation, item)` tuples where operation ∈ `{CREATE, UPDATE, DELETE, IMPORT}`
- `run(state)`: executes the plan — order is CREATE then UPDATE then DELETE
- `verify(state)`: independent security/config audit — reads live AWS state for every node and edge, calls each object's `verify()` method, prints pass/fail results, returns total failure count (for exit-code CI gating)
- `export_graph(state)`: renders the graph as a Graphviz DOT/PNG diagram

### State Model

Three layers are reconciled on every `plan()`:
```
AWS live state  ↔  SQLite DB (last applied)  ↔  Code definition
                              ↓
                  Diff → CREATE | IMPORT | UPDATE | DELETE
```

SQLite has two tables: `nodes` and `edges`, each storing JSON-serialized Pydantic models. `db.py` owns all reads/writes.

### Node and Edge Base Classes (`src/GraphIaC/models.py`)

Every AWS resource subclasses `BaseNode`; every relationship subclasses `BaseEdge`. Both are Pydantic models. Each concrete class must implement:
- `read(session, G, g_id, read_id)` — fetch live AWS state, return a new instance or `None`
- `create(session, G)` — call boto3 to provision
- `update(session, G, diff)` — apply changes from DeepDiff output
- `delete(session, G)` — deprovision
- `verify(session, G) -> list[VerifyResult]` — optional; return pass/fail checks for the `verify` command

`g_id` is the stable local identifier; `read_id` is the AWS resource ID (often different).

**`VerifyResult`** is a Pydantic model with `name: str`, `passed: bool`, `message: str = ""`. Return one per check from `verify()`.

**Diff behavior:** `BaseNode.diff()` and `BaseEdge.diff()` only compare fields that `self` has set to a non-None value. This means sparse infra.py definitions (e.g. `HostedZone(g_id="hz", domain_name="begrif.co")` with `zone_id=None`) do not false-positive against fully-populated AWS state. Only fields the user explicitly set are checked for drift.

### AWS Modules (`src/GraphIaC/aws/`)

Each file owns one AWS service. Typical pattern: one `BaseNode` subclass (the resource) plus one or more `BaseEdge` subclasses (the IAM policies or service integrations it needs).

Examples of the edge pattern:
- `IAMRolePolicyLambdaEdge` — attaches `AWSLambdaBasicExecutionRole` when a Lambda node connects to an IAM role
- `EndpointLambdaEdge` — wires an API Gateway route to a Lambda integration + grants invoke permission
- `SiteEndpointEdge` — connects an API Gateway HTTP API to a route
- `ACMCertificateHostedZoneEdge` — adds Route53 CNAME validation records so ACM can issue the cert
- `CloudFrontS3OACEdge` — sets an S3 bucket policy allowing only a specific CloudFront distribution (OAC pattern)
- `CloudFrontRoute53Edge` — creates a Route53 A alias record pointing a domain at a CloudFront distribution; reads the CF domain name from the graph at `create()` time so it works in the same run as CF creation
- `CloudFrontFunctionEdge` — associates a `CloudFrontFunction` with a distribution's default cache behavior on the viewer-request event; knows which event type and how to patch the distribution config

Current node inventory by service:
- **ACM** (`certificate.py`): `ACMCertificate`
- **CloudFront** (`cloudfront.py`): `CloudFrontDistribution`, `CloudFrontFunction`
- **Route53** (`route53.py`): `HostedZone`, `Route53AliasRecord`
- **S3** (`s3.py`): `S3Bucket`
- **IAM** (`iam_role.py`, `iam_policy.py`): `IAMRole`
- **Lambda** (`lambda_func.py`): `LambdaZipFile`
- **DynamoDB** (`dynamodb.py`): `DynamoTable`
- **API Gateway** (`apigateway.py`): `ApiSite`, `ApiEndpoint`
- **SES** (`ses.py`): `SESDomainIdentity`

### Two-Phase Pattern for Long-Running Resources

Some AWS resources take hours to provision (ACM certificate validation is the main case). The correct pattern is **conditional graph building** based on live resource state — not framework-level waiting or retries:

```python
def infra(state):
    # Phase 1: always add the slow resource
    cert = ACMCertificate(g_id="cert", domain_name="example.com")
    GraphIaC.add_node(state, cert)

    # Phase 2: check live state; skip downstream until ready
    live_cert = ACMCertificate.read(state.session, state.G, "cert", None)
    if not (live_cert and live_cert.status == "ISSUED"):
        return

    # downstream resources that depend on the cert...
```

The `read()` classmethod searches AWS by domain name when no ARN is provided yet (`read_id=None`). Re-run after the resource is ready and the downstream graph gets built automatically.

### Model Registry (`src/GraphIaC/model_map.py`)

Maps string type names to classes. Required so the DB layer can deserialize stored JSON back into the correct Pydantic model type. **When adding a new node or edge class, register it here.**

### The DSL and Browser Sandbox (`dsl/`, `src/GraphIaC/web/`)

A small declarative language over nodes and edges — spec in `dsl/spec.md`. The DSL is purely a frontend: it parses down to the same flat node/edge structure the engine consumes. Key pieces:

- `src/GraphIaC/web/graphiac.js` — the pure JS parser core (UMD, no DOM, no AWS knowledge) powering the live editor.
- `src/GraphIaC/dsl.py` — the Python parser, the engine's side of the language. The two are deliberate twins: same grammar, same graph JSON, same error wording, byte-identical `desugar()` output. **When changing one parser, change the other** — both must satisfy the shared fixture corpus in `dsl/fixtures/` (`*.giac` source → expected `*.json` parse result).
- `src/GraphIaC/web/registry.js` — GENERATED. All AWS type knowledge for the JS parser, introspected from the Pydantic models. Regenerate after changing any model or `model_map.py`: `python -m GraphIaC.dsl_registry`. The name-field and edge-endpoint tables live in `dsl_registry.py`; new node/edge classes must be added there too to be usable from the DSL.
- `src/GraphIaC/web/index.html` — the live sandbox (editor + graph diagram + desugar). Open directly in a browser; no build step, no npm — keep it that way. Shipped inside the wheel as package data.
- `dsl.load_graph(state, graph)` — instantiates a parsed graph into a `GraphIaCState`, resolving attribute references (`$ref`) from live AWS state. Unresolvable refs make the node — and everything touching it — **BLOCKED**: reported by `plan(state, blocked=...)`, skipped by `run`, shielded from orphan deletion, and picked up automatically on a later run once the upstream is ready (gated by each node's `ready()`, e.g. ACM cert must be ISSUED). This replaces the two-phase pattern for DSL infra.
- CLI: `.giac` files work anywhere `.py` infra files do — `python -m GraphIaC <profile> --infra_file site.giac plan|run|verify|diagram`.

Run the DSL tests: `pytest tests/test_dsl.py tests/test_dsl_load.py` and `node --test src/GraphIaC/web/`

### Logging (`src/GraphIaC/logs.py`)

Shared `colorlog` setup. Call `setup_logger()` in new modules rather than calling `logging` directly.

## Adding a New AWS Resource

1. Create `src/GraphIaC/aws/<service>.py` with a `BaseNode` subclass and any needed `BaseEdge` subclasses
2. Implement `read`, `create`, `update`, `delete` on each
3. Register the new classes in `model_map.py`
4. Export from `src/GraphIaC/__init__.py` if user-facing
5. Add an example under `examples/`
