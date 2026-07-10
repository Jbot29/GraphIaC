"""Deploy-policy generation and the DeployRole: the graph knows its own
permissions."""

import sqlite3
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

import GraphIaC
from GraphIaC import deploy_policy, dsl
from GraphIaC.aws.deploy_role import POLICY_NAME, DeployRole
from GraphIaC.main import OperationType

FIXTURES = Path(__file__).parent.parent / "dsl" / "fixtures"


def _actions(policy):
    out = set()
    for stmt in policy["Statement"]:
        out.update(stmt["Action"])
    return out


def _policy_for(fixture):
    graph = dsl.parse((FIXTURES / fixture).read_text())["graph"]
    return deploy_policy.policy_for_graph(graph)


def test_static_site_policy_is_minimal_and_sufficient():
    actions = _actions(_policy_for("static-site-edge.giac"))
    # what the stack needs...
    assert {"s3:CreateBucket", "acm:RequestCertificate", "cloudfront:CreateDistribution",
            "route53:ChangeResourceRecordSets"} <= actions
    # ...and nothing from services it doesn't touch
    assert not any(a.startswith(("dynamodb:", "cognito-idp:", "lambda:")) for a in actions)


def test_lambda_ui_policy_covers_every_wall_hit_in_the_field():
    """Every permission failure from the real lambda-ui deploy session must
    be present in its generated policy."""
    actions = _actions(_policy_for("lambda-ui.giac"))
    for action in ["cognito-idp:CreateUserPool", "iam:CreateRole", "iam:PassRole",
                   "lambda:AddPermission", "lambda:CreateFunctionUrlConfig",
                   "iam:UpdateAssumeRolePolicy", "cognito-idp:AdminGetUser"]:
        assert action in actions, action


def test_statements_group_by_service():
    policy = _policy_for("bucket.giac")
    sids = [s["Sid"] for s in policy["Statement"]]
    assert "GraphIaCS3" in sids
    for stmt in policy["Statement"]:
        prefixes = {a.split(":")[0] for a in stmt["Action"]}
        assert len(prefixes) == 1  # one service per statement

def test_unknown_type_raises():
    with pytest.raises(KeyError, match="Bogus"):
        deploy_policy.actions_for_types(["Bogus"])


def test_policy_for_all_covers_every_registered_type():
    from GraphIaC.model_map import BASE_MODEL_MAP

    actions = _actions(deploy_policy.policy_for_all())
    for cls in BASE_MODEL_MAP.values():
        assert set(cls.deploy_actions) <= actions


# ---------------------------------------------------------------------
# DeployRole against moto
# ---------------------------------------------------------------------
@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    with mock_aws():
        yield boto3.session.Session(region_name="us-east-2")


def load_setup(aws, conn):
    state = GraphIaC.init(aws, conn)
    src = (Path(__file__).parent.parent / "examples" / "get-started" / "setup.giac").read_text()
    res = dsl.parse(src)
    assert res["errors"] == []
    assert dsl.load_graph(state, res["graph"]) == []
    return state


def test_setup_giac_creates_synced_role(aws, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "s.db"))

    state = load_setup(aws, conn)
    assert [op.operation for op in GraphIaC.plan(state)] == [OperationType.CREATE]
    GraphIaC.run(load_setup(aws, conn))

    iam = aws.client("iam")
    role = iam.get_role(RoleName="graphiac-deploy")["Role"]
    trust = role["AssumeRolePolicyDocument"]
    principal = trust["Statement"][0]["Principal"]["AWS"]
    assert principal.endswith(":root")  # account-root trust

    doc = iam.get_role_policy(RoleName="graphiac-deploy", PolicyName=POLICY_NAME)[
        "PolicyDocument"
    ]
    live = set()
    for stmt in doc["Statement"]:
        live.update(stmt["Action"])
    assert _actions(deploy_policy.policy_for_all()) <= live

    # converged: role exists, policy in sync -> empty plan
    assert GraphIaC.plan(load_setup(aws, conn)) == []

    # verify: both checks green
    checks = DeployRole(g_id="d", name="graphiac-deploy").verify(aws, state.G)
    assert all(c.passed for c in checks)


def test_stale_policy_triggers_update_and_resync(aws, tmp_path):
    import json

    conn = sqlite3.connect(str(tmp_path / "s.db"))
    GraphIaC.run(load_setup(aws, conn))

    # simulate an old install: shrink the live policy
    iam = aws.client("iam")
    iam.put_role_policy(
        RoleName="graphiac-deploy", PolicyName=POLICY_NAME,
        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [
            {"Sid": "Old", "Effect": "Allow", "Action": ["s3:CreateBucket"], "Resource": "*"}
        ]}),
    )
    checks = DeployRole(g_id="d", name="graphiac-deploy").verify(aws, None)
    assert not all(c.passed for c in checks)  # verify flags the gap

    # plan sees drift, run re-syncs, verify passes again
    ops = [op.operation for op in GraphIaC.plan(load_setup(aws, conn))]
    assert ops == [OperationType.UPDATE]
    GraphIaC.run(load_setup(aws, conn))
    checks = DeployRole(g_id="d", name="graphiac-deploy").verify(aws, None)
    assert all(c.passed for c in checks)
