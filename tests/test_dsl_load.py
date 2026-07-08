"""Tests for dsl.load_graph and the BLOCKED planner path.

Two layers:
  - Fake-node tests: the ref-resolution / blocking mechanics, no AWS at all.
  - moto tests: the real flow end to end — a .giac source planned and run
    against mocked AWS, including the two-phase ACM pattern where the
    CloudFront half of the graph stays BLOCKED until the cert is ISSUED.
"""

import sqlite3
from pathlib import Path
from typing import ClassVar, Optional

import boto3
import pytest
from moto import mock_aws

import GraphIaC
from GraphIaC import dsl
from GraphIaC.db import db_create_node
from GraphIaC.dsl import BlockedItem
from GraphIaC.main import OperationType
from GraphIaC.models import BaseEdge, BaseNode

FIXTURES = Path(__file__).parent.parent / "dsl" / "fixtures"


# ---------------------------------------------------------------------
# fakes — controllable live state, no AWS
# ---------------------------------------------------------------------
class FakeNode(BaseNode):
    LIVE: ClassVar[dict] = {}  # g_id -> the instance read() returns

    arn: Optional[str] = None
    dep_arn: Optional[str] = None
    is_ready: bool = True

    @classmethod
    def read(cls, session, G, g_id, read_id):
        return cls.LIVE.get(g_id)

    def ready(self):
        return self.is_ready


class FakeEdge(BaseEdge):
    a_g_id: str
    b_g_id: str

    @property
    def source_g_id(self):
        return self.a_g_id

    @property
    def destination_g_id(self):
        return self.b_g_id


@pytest.fixture
def state():
    FakeNode.LIVE = {}
    s = GraphIaC.init(None, sqlite3.connect(":memory:"))
    s.models_map = {"FakeNode": FakeNode, "FakeEdge": FakeEdge}
    return s


def node(g_id, fields=None):
    return {"g_id": g_id, "type": "FakeNode", "fields": fields or {}, "line": 1}


def ref(g_id, field="arn"):
    return {"$ref": {"g_id": g_id, "field": field}}


def graph(nodes, edges=()):
    return {"nodes": list(nodes), "edges": list(edges)}


# ---------------------------------------------------------------------
# ref resolution and blocking mechanics
# ---------------------------------------------------------------------
def test_plain_nodes_load(state):
    blocked = dsl.load_graph(state, graph([node("a"), node("b")]))
    assert blocked == []
    assert set(state.G.nodes) == {"a", "b"}


def test_ref_resolves_from_live_state(state):
    FakeNode.LIVE["a"] = FakeNode(g_id="a", arn="arn:a")
    blocked = dsl.load_graph(state, graph([node("a"), node("b", {"dep_arn": ref("a")})]))
    assert blocked == []
    assert state.G.nodes["b"]["data"].dep_arn == "arn:a"


def test_declaration_order_does_not_matter(state):
    FakeNode.LIVE["a"] = FakeNode(g_id="a", arn="arn:a")
    blocked = dsl.load_graph(state, graph([node("b", {"dep_arn": ref("a")}), node("a")]))
    assert blocked == []
    assert state.G.nodes["b"]["data"].dep_arn == "arn:a"


def test_ref_blocks_until_target_exists(state):
    blocked = dsl.load_graph(state, graph([node("a"), node("b", {"dep_arn": ref("a")})]))
    assert [b.g_id for b in blocked] == ["b"]
    assert "not created yet" in blocked[0].reason
    assert "b" not in state.G  # a still loads
    assert "a" in state.G


def test_ref_blocks_until_target_ready(state):
    FakeNode.LIVE["a"] = FakeNode(g_id="a", arn="arn:a", is_ready=False)
    blocked = dsl.load_graph(state, graph([node("a"), node("b", {"dep_arn": ref("a")})]))
    assert "exists but not ready" in blocked[0].reason


def test_ref_blocks_until_field_has_value(state):
    FakeNode.LIVE["a"] = FakeNode(g_id="a")  # live and ready, but arn is None
    blocked = dsl.load_graph(state, graph([node("a"), node("b", {"dep_arn": ref("a")})]))
    assert 'waiting on "a.arn"' in blocked[0].reason


def test_blocking_cascades_downstream(state):
    g = graph([
        node("c", {"dep_arn": ref("b")}),
        node("b", {"dep_arn": ref("a")}),
        node("a"),
    ])
    blocked = dsl.load_graph(state, g)
    assert {b.g_id for b in blocked} == {"b", "c"}
    reasons = {b.g_id: b.reason for b in blocked}
    assert "not created yet" in reasons["b"]
    assert '"b"' in reasons["c"]  # blocked because b is blocked


def test_edge_blocks_when_endpoint_blocked(state):
    g = graph(
        [node("a"), node("b", {"dep_arn": ref("a")})],
        [{"type": "FakeEdge", "fields": {"a_g_id": "a", "b_g_id": "b"}, "inferred": True, "line": 3}],
    )
    blocked = dsl.load_graph(state, g)
    assert len(state.G.edges) == 0
    edge_block = next(b for b in blocked if b.type == "FakeEdge")
    assert edge_block.g_id == "a → b"
    assert 'waiting on blocked node "b"' in edge_block.reason


def test_edge_loads_when_endpoints_do(state):
    g = graph(
        [node("a"), node("b")],
        [{"type": "FakeEdge", "fields": {"a_g_id": "a", "b_g_id": "b"}, "inferred": True, "line": 3}],
    )
    assert dsl.load_graph(state, g) == []
    assert ("a", "b") in state.G.edges


def test_circular_refs_block(state):
    g = graph([node("a", {"dep_arn": ref("b")}), node("b", {"dep_arn": ref("a")})])
    blocked = dsl.load_graph(state, g)
    assert {b.g_id for b in blocked} == {"a", "b"}
    assert all("circular" in b.reason for b in blocked)


def test_self_ref_blocks(state):
    blocked = dsl.load_graph(state, graph([node("a", {"dep_arn": ref("a")})]))
    assert "references itself" in blocked[0].reason


# ---------------------------------------------------------------------
# the planner: BLOCKED operations and delete protection
# ---------------------------------------------------------------------
def test_plan_reports_blocked_and_shields_db_row(state):
    # "cf" was provisioned on an earlier run (it has a DB row) but is blocked
    # now — plan must NOT read that as "removed from the code" and delete it.
    db_create_node(state.db_conn, FakeNode(g_id="cf"))
    b = BlockedItem(g_id="cf", type="FakeNode", reason="waiting on cert.arn")

    ops = GraphIaC.plan(state, blocked=[b])
    assert [op.operation for op in ops] == [OperationType.BLOCKED]

    # control: without the blocked marker the same DB row reads as an orphan
    ops = GraphIaC.plan(state, blocked=[])
    assert [op.operation for op in ops] == [OperationType.DELETE]


def test_run_skips_blocked(state):
    b = BlockedItem(g_id="x", type="FakeNode", reason="waiting")
    GraphIaC.run(state, blocked=[b])  # must not raise / touch anything


# ---------------------------------------------------------------------
# the real thing, against moto: .giac -> load -> plan -> run
# ---------------------------------------------------------------------
@pytest.fixture
def aws(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    # moto reads MOTO_ACM_VALIDATION_WAIT at import time, so patch the
    # setting itself: certs go ISSUED on the first describe
    monkeypatch.setattr("moto.settings.ACM_VALIDATION_WAIT", 0)
    with mock_aws():
        session = boto3.session.Session(region_name="us-east-1")
        conn = sqlite3.connect(str(tmp_path / "state.db"))
        yield session, conn


def load_giac(session, conn, src):
    state = GraphIaC.init(session, conn)
    res = dsl.parse(src)
    assert res["errors"] == []
    blocked = dsl.load_graph(state, res["graph"])
    return state, blocked


def test_giac_bucket_plan_run_converges(aws):
    session, conn = aws
    src = "site-bucket : S3Bucket\n"

    state, blocked = load_giac(session, conn, src)
    assert blocked == []
    ops = GraphIaC.plan(state, blocked)
    assert [op.operation for op in ops] == [OperationType.CREATE]

    GraphIaC.run(state, blocked)
    session.client("s3").head_bucket(Bucket="site-bucket")  # it really exists

    # second run from scratch: nothing to do
    state, blocked = load_giac(session, conn, src)
    assert GraphIaC.plan(state, blocked) == []


def test_static_site_blocks_then_unblocks(aws):
    session, conn = aws
    src = (FIXTURES / "static-site.giac").read_text()

    # phase 1: no certificate exists — the CloudFront half is BLOCKED
    state, blocked = load_giac(session, conn, src)
    assert {b.g_id for b in blocked} == {"cf", "cf → bucket", "cf → hz"}
    assert 'waiting on "cert"' in next(b for b in blocked if b.g_id == "cf").reason

    ops = GraphIaC.plan(state, blocked)
    by_type = {}
    for op in ops:
        by_type.setdefault(op.operation, []).append(op)
    assert len(by_type[OperationType.CREATE]) == 3       # hz, cert, bucket
    assert len(by_type[OperationType.CREATE_EDGE]) == 1  # cert -> hz validation
    assert len(by_type[OperationType.BLOCKED]) == 3      # cf + its two edges
    assert OperationType.DELETE not in by_type

    # the certificate gets requested and (moto, wait=0) ISSUED
    session.client("acm", region_name="us-east-1").request_certificate(
        DomainName="begrif.co", ValidationMethod="DNS"
    )

    # phase 2: same source, nothing changed by the author — cf unblocks,
    # its cert_arn resolved from live state
    state, blocked = load_giac(session, conn, src)
    assert blocked == []
    cf = state.G.nodes["cf"]["data"]
    assert cf.cert_arn.startswith("arn:aws:acm:")
    assert ("cf", "bucket") in state.G.edges
    assert ("cf", "hz") in state.G.edges
