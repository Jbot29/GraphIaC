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

## Architecture

### Core Engine (`src/GraphIaC/main.py`)

Four public functions form the user-facing API: `init()`, `add_node()`, `plan()`, `run()`.

- `init(session, db_conn)` → `GraphIaCState`: creates a NetworkX DiGraph and connects to the SQLite state DB
- `plan(state)`: reads current AWS state for every node/edge via boto3, diffs against the local DB, returns a list of `(operation, item)` tuples where operation ∈ `{CREATE, UPDATE, DELETE, IMPORT}`
- `run(state)`: executes the plan — order is CREATE then UPDATE then DELETE
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

`g_id` is the stable local identifier; `read_id` is the AWS resource ID (often different).

### AWS Modules (`src/GraphIaC/aws/`)

Each file owns one AWS service. Typical pattern: one `BaseNode` subclass (the resource) plus one or more `BaseEdge` subclasses (the IAM policies or service integrations it needs).

Examples of the edge pattern:
- `IAMRolePolicyLambdaEdge` — attaches `AWSLambdaBasicExecutionRole` when a Lambda node connects to an IAM role
- `EndpointLambdaEdge` — wires an API Gateway route to a Lambda integration + grants invoke permission
- `SiteEndpointEdge` — connects an API Gateway HTTP API to a route

### Model Registry (`src/GraphIaC/model_map.py`)

Maps string type names to classes. Required so the DB layer can deserialize stored JSON back into the correct Pydantic model type. **When adding a new node or edge class, register it here.**

### Logging (`src/GraphIaC/logs.py`)

Shared `colorlog` setup. Import `get_logger(__name__)` in new modules rather than calling `logging` directly.

## Adding a New AWS Resource

1. Create `src/GraphIaC/aws/<service>.py` with a `BaseNode` subclass and any needed `BaseEdge` subclasses
2. Implement `read`, `create`, `update`, `delete` on each
3. Register the new classes in `model_map.py`
4. Export from `src/GraphIaC/__init__.py` if user-facing
5. Add an example under `examples/`
