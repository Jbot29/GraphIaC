"""Edge read() idempotence: after create(), read() must see the wiring —
otherwise plan re-applies the edge forever."""

import sqlite3

import boto3
import networkx as nx
import pytest
from moto import mock_aws

import GraphIaC
from GraphIaC.aws.iam_role import IAMRole
from GraphIaC.aws.lambda_func import IAMRolePolicyLambdaEdge
from GraphIaC.aws.route53 import HostedZone
from GraphIaC.aws.ses import SESDomainIdentity, SESDomainRoute53Edge


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("MOTO_IAM_LOAD_MANAGED_POLICIES", "true")  # AWSLambdaBasicExecutionRole
    with mock_aws():
        yield boto3.session.Session(region_name="us-east-1")


def test_role_policy_edge_read_sees_attachment(aws):
    G = nx.DiGraph()
    role = IAMRole(g_id="role", name="edge-read-role")
    G.add_node("role", data=role)
    edge = IAMRolePolicyLambdaEdge(role_g_id="role", node_g_id="fn")

    # role doesn't exist yet -> not attached (and no crash)
    assert edge.read(aws, G) is None

    role.create(aws, G)
    assert edge.read(aws, G) is None  # role exists, policy not attached

    edge.create(aws, G)
    assert edge.read(aws, G) is edge  # attached -> converged


def test_ses_dkim_edge_read_sees_records_regardless_of_token_order(aws):
    G = nx.DiGraph()
    aws.client("route53").create_hosted_zone(Name="edge.co", CallerReference="t")

    hz = HostedZone.read(aws, G, "hz", "edge.co")
    ses = SESDomainIdentity(g_id="ses", domain="edge.co")
    ses.create(aws, G)
    # moto's sesv2 returns no tokens; pin them, deliberately mis-sorted —
    # read() must not depend on token order (the bug this test pins)
    ses.dkim_tokens = ["zzz2222", "aaa1111", "mmm3333"]
    G.add_node("hz", data=hz)
    G.add_node("ses", data=ses)
    edge = SESDomainRoute53Edge(ses_g_id="ses", zone_g_id="hz")

    assert edge.read(aws, G) is None  # records not created yet

    edge.create(aws, G)
    assert edge.read(aws, G) is edge  # all three DKIM records found


def test_plan_converges_for_role_policy_edge(aws):
    """The user-visible symptom: run once, plan again — no re-apply."""
    state = GraphIaC.init(aws, sqlite3.connect(":memory:"))
    role = IAMRole(g_id="role", name="conv-role")
    edge = IAMRolePolicyLambdaEdge(role_g_id="role", node_g_id="role2")
    role2 = IAMRole(g_id="role2", name="conv-role2")  # stand-in destination node
    GraphIaC.add_node(state, role)
    GraphIaC.add_node(state, role2)
    GraphIaC.add_edge(state, edge)

    GraphIaC.run(state)
    assert GraphIaC.plan(state) == []
