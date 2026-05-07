# GraphIaC

A graph-based Infrastructure-as-Code framework for AWS. Model your cloud infrastructure as a directed graph — **nodes are AWS resources, edges are the connections and permissions between them**.

## The Problem

Tools like Terraform and Pulumi make it easy to define individual resources. The hard part is wiring them together: IAM policies, invoke permissions, service integrations. That boilerplate is repetitive, error-prone, and the main source of permission debugging in real projects. Most IaC tools treat it as an afterthought, burying it inside resource definitions in ways that make the code hard to read and impossible to reuse.

## The Approach

GraphIaC promotes connections to first-class citizens. When you add a `LambdaToDynamoEdge`, the edge already knows what IAM policies are required. It queries the graph for the relevant ARNs and provisions everything itself. You declare the connection; the edge handles the boilerplate.

Because AWS permission patterns are stable, that knowledge gets written once into the edge class and reused everywhere. Nodes stay clean and self-contained, which means they're also easy to copy across projects.

A secondary benefit: because the infrastructure is a graph, you can render it as a diagram at any time — always up to date, no manual documentation required.

## Key Concepts

- **Nodes** — AWS resources (Lambda, DynamoDB table, IAM role, API Gateway, etc.), each a Pydantic model with `read`, `create`, `update`, and `delete` methods
- **Edges** — the connections between resources; each edge knows what it takes to wire two nodes together (IAM policies, invoke permissions, etc.)
- **State reconciliation** — on every `plan()`, GraphIaC diffs live AWS state against a local SQLite DB and produces a list of `CREATE`, `UPDATE`, `DELETE`, or `IMPORT` operations
- **`run()`** — executes the plan in the correct order

## Install

```bash
pip install -e ".[dev]"
```

GraphIaC can export infrastructure diagrams via Graphviz. To enable that, install `pygraphviz`:

```bash
pip install --config-settings="--global-option=build_ext" \
            --config-settings="--global-option=-I$(brew --prefix graphviz)/include/" \
            --config-settings="--global-option=-L$(brew --prefix graphviz)/lib/" \
            pygraphviz
```

## Usage

```python
import sqlite3
import boto3
import GraphIaC
from GraphIaC.aws.dynamodb import DynamoTable, DynamoKey
from GraphIaC.aws.lambda_func import LambdaFunction

session = boto3.Session(profile_name="my-profile")
db_conn = sqlite3.connect("my-infra.db")

state = GraphIaC.init(session, db_conn)

table = DynamoTable(g_id="users_table", table_name="users", partition_key=DynamoKey(name="pk", attr_type="S"))
GraphIaC.add_node(state, table)

# plan() shows what will change; run() applies it
GraphIaC.plan(state)
GraphIaC.run(state)
```

## Running Tests

Tests mirror the source tree (`tests/aws/` covers `src/GraphIaC/aws/`, and so on for future providers).

**AWS integration tests** hit real AWS and require credentials. Set `AWS_PROFILE` to the profile you want to use, then run:

```bash
AWS_PROFILE=your-profile pytest tests/aws/
```

Tests generate randomized resource names on every run and clean up after themselves, including on failure. Resources are created in `us-east-2` by default.

**Run a specific test file:**
```bash
AWS_PROFILE=your-profile pytest tests/aws/test_dynamodb.py -v
```

**Skip AWS tests** (e.g. in CI without credentials) — omit `AWS_PROFILE` or unset it. Any test that needs AWS will be skipped automatically.
