"""Cognito against moto: node lifecycle, edge idempotence, verify checks,
and the .giac end-to-end (plan -> run -> converged plan)."""

import sqlite3

import boto3
import networkx as nx
import pytest
from moto import mock_aws

import GraphIaC
from GraphIaC import dsl
from GraphIaC.aws.cognito import CognitoPoolClientEdge, CognitoUserPool, CognitoUserPoolClient
from GraphIaC.main import OperationType

REGION = "us-east-2"

SRC = (
    'users : CognitoUserPool(region: "us-east-2")\n'
    'ui    : CognitoUserPoolClient(callback_urls: ["https://ui.example.com/cb"])\n'
    "users -> ui\n"
)


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        yield boto3.session.Session(region_name=REGION)


def test_pool_create_read_by_name_and_id(aws):
    G = nx.DiGraph()
    pool = CognitoUserPool(g_id="p", pool_name="test-pool", region=REGION)
    assert CognitoUserPool.read(aws, G, "p", "test-pool", region=REGION) is None

    pool.create(aws, G)
    assert pool.pool_id

    by_name = CognitoUserPool.read(aws, G, "p", "test-pool", region=REGION)
    assert by_name.pool_id == pool.pool_id
    assert by_name.admin_only_signup is True
    assert by_name.password_min_length == 12

    by_id = CognitoUserPool.read(aws, G, "p", pool.pool_id, region=REGION)
    assert by_id.pool_name == "test-pool"


def test_pool_verify_flags_open_signup(aws):
    G = nx.DiGraph()
    pool = CognitoUserPool(g_id="p", pool_name="open-pool", region=REGION,
                           admin_only_signup=False, password_min_length=8)
    pool.create(aws, G)
    results = {r.name: r.passed for r in pool.verify(aws, G)}
    assert results["Self-signup disabled"] is False
    assert results["Password minimum length"] is False
    assert results["Deletion protection"] is True


def test_client_edge_read_after_create(aws):
    G = nx.DiGraph()
    pool = CognitoUserPool(g_id="p", pool_name="edge-pool", region=REGION)
    client = CognitoUserPoolClient(g_id="c", client_name="edge-client",
                                   callback_urls=["https://x.co/cb"])
    G.add_node("p", data=pool)
    G.add_node("c", data=client)
    edge = CognitoPoolClientEdge(pool_g_id="p", client_g_id="c")

    pool.create(aws, G)
    assert edge.read(aws, G) is None  # pool exists, client doesn't

    edge.create(aws, G)
    assert client.client_id
    assert edge.read(aws, G) is edge  # converged

    checks = {r.name: r.passed for r in edge.verify(aws, G)}
    assert checks["App client exists"] is True
    assert checks["User existence errors hidden"] is True


def test_giac_end_to_end_converges(aws, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "state.db"))

    def load():
        state = GraphIaC.init(aws, conn)
        res = dsl.parse(SRC)
        assert res["errors"] == []
        blocked = dsl.load_graph(state, res["graph"])
        assert blocked == []
        return state

    state = load()
    ops = [op.operation for op in GraphIaC.plan(state)]
    # pool CREATEs; the metadata-only client node IMPORTs (ApiEndpoint pattern)
    assert ops == [OperationType.CREATE, OperationType.IMPORT, OperationType.CREATE_EDGE]

    GraphIaC.run(load())

    # the pool and client really exist, with the label-derived names
    idp = aws.client("cognito-idp", region_name=REGION)
    pools = idp.list_user_pools(MaxResults=10)["UserPools"]
    assert [p["Name"] for p in pools] == ["users"]
    clients = idp.list_user_pool_clients(UserPoolId=pools[0]["Id"], MaxResults=10)[
        "UserPoolClients"
    ]
    assert [c["ClientName"] for c in clients] == ["ui"]

    # converged: nothing to do
    assert GraphIaC.plan(load()) == []
