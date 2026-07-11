"""The lambda-ui base against moto: function URLs, the Cognito auth edge,
the .giac end-to-end, and the miniui example app itself (login page,
sessions, static assets, API dispatch) driven through simulated
function-URL events."""

import io
import sqlite3
import sys
import zipfile
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

import GraphIaC
from GraphIaC import dsl
from GraphIaC.aws.cognito import (
    CognitoLambdaAuthEdge,
    CognitoPoolClientEdge,
    CognitoUserPool,
    CognitoUserPoolClient,
)
from GraphIaC.aws.iam_role import IAMRole
from GraphIaC.aws.lambda_func import IAMRolePolicyLambdaEdge, LambdaZipFile

REGION = "us-east-2"
EXAMPLE = Path(__file__).parent.parent / "examples" / "lambda-ui"


@pytest.fixture
def aws(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("MOTO_IAM_LOAD_MANAGED_POLICIES", "true")
    with mock_aws():
        yield boto3.session.Session(region_name=REGION)


@pytest.fixture
def zip_path(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app.py", "def handler(event, context):\n    return {}\n")
    p = tmp_path / "deployment.zip"
    p.write_bytes(buf.getvalue())
    return str(p)


def build_graph(state, zip_path):
    GraphIaC.add_node(state, CognitoUserPool(g_id="users", pool_name="ui-users", region=REGION))
    GraphIaC.add_node(state, CognitoUserPoolClient(g_id="login", client_name="ui-login",
                                                   password_auth=True))
    GraphIaC.add_node(state, IAMRole(g_id="role", name="ui-role"))
    GraphIaC.add_node(state, LambdaZipFile(g_id="app", name="ui-app", runtime="python3.13",
                                           handler="app.handler", zip_file_path=zip_path,
                                           region=REGION, public_url=True))
    GraphIaC.add_edge(state, IAMRolePolicyLambdaEdge(role_g_id="role", node_g_id="app"))
    GraphIaC.add_edge(state, CognitoPoolClientEdge(pool_g_id="users", client_g_id="login"))
    GraphIaC.add_edge(state, CognitoLambdaAuthEdge(client_g_id="login", fn_g_id="app"))


def test_run_wires_url_and_auth_env(aws, zip_path, tmp_path):
    state = GraphIaC.init(aws, sqlite3.connect(str(tmp_path / "s.db")))
    build_graph(state, zip_path)
    GraphIaC.run(state)

    lc = aws.client("lambda", region_name=REGION)
    url = lc.get_function_url_config(FunctionName="ui-app")["FunctionUrl"]
    assert url.startswith("https://")

    # BOTH public grants (AWS requires the pair since Oct 2025; missing
    # either one = 403 Forbidden at the URL)
    import json as _json

    policy = _json.loads(lc.get_policy(FunctionName="ui-app")["Policy"])
    actions = {s["Action"] for s in policy["Statement"]}
    assert {"lambda:InvokeFunctionUrl", "lambda:InvokeFunction"} <= actions

    env = lc.get_function_configuration(FunctionName="ui-app")["Environment"]["Variables"]
    idp = aws.client("cognito-idp", region_name=REGION)
    pool_id = idp.list_user_pools(MaxResults=10)["UserPools"][0]["Id"]
    client_id = idp.list_user_pool_clients(UserPoolId=pool_id, MaxResults=10)[
        "UserPoolClients"
    ][0]["ClientId"]
    assert env["COGNITO_POOL_ID"] == pool_id
    assert env["COGNITO_CLIENT_ID"] == client_id
    assert env["COGNITO_REGION"] == REGION

    # password auth is enabled on the client (the miniui login flow)
    client = idp.describe_user_pool_client(UserPoolId=pool_id, ClientId=client_id)[
        "UserPoolClient"
    ]
    assert "ALLOW_USER_PASSWORD_AUTH" in client["ExplicitAuthFlows"]


def test_auth_edge_read_idempotent(aws, zip_path, tmp_path):
    state = GraphIaC.init(aws, sqlite3.connect(str(tmp_path / "s.db")))
    build_graph(state, zip_path)

    edge = CognitoLambdaAuthEdge(client_g_id="login", fn_g_id="app")
    assert edge.read(aws, state.G) is None  # nothing exists yet

    GraphIaC.run(state)
    assert edge.read(aws, state.G) is edge  # env matches -> converged

    # env merge: our vars must not clobber others
    lc = aws.client("lambda", region_name=REGION)
    env = lc.get_function_configuration(FunctionName="ui-app")["Environment"]["Variables"]
    lc.update_function_configuration(
        FunctionName="ui-app", Environment={"Variables": {**env, "MY_VAR": "keep-me"}}
    )
    edge.create(aws, state.G)
    env = lc.get_function_configuration(FunctionName="ui-app")["Environment"]["Variables"]
    assert env["MY_VAR"] == "keep-me"


def test_fixture_giac_loads(aws, zip_path, tmp_path):
    src = (Path(__file__).parent.parent / "dsl" / "fixtures" / "lambda-ui.giac").read_text()
    src = src.replace('"./deployment.zip"', f'"{zip_path}"')
    state = GraphIaC.init(aws, sqlite3.connect(str(tmp_path / "s.db")))
    res = dsl.parse(src)
    assert res["errors"] == []
    assert dsl.load_graph(state, res["graph"]) == []
    GraphIaC.run(state)
    lc = aws.client("lambda", region_name=REGION)
    assert lc.get_function_url_config(FunctionName="app")["FunctionUrl"]


# ---------------------------------------------------------------------
# the example app itself, driven through function-URL events
# ---------------------------------------------------------------------
@pytest.fixture
def miniui_app(aws, monkeypatch):
    """The example's real code, wired to a real (moto) pool with one user."""
    idp = aws.client("cognito-idp", region_name=REGION)
    pool_id = idp.create_user_pool(PoolName="mini")["UserPool"]["Id"]
    client_id = idp.create_user_pool_client(
        UserPoolId=pool_id, ClientName="mini",
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )["UserPoolClient"]["ClientId"]
    idp.admin_create_user(UserPoolId=pool_id, Username="me@example.com",
                          MessageAction="SUPPRESS")
    idp.admin_set_user_password(UserPoolId=pool_id, Username="me@example.com",
                                Password="CorrectHorse9!", Permanent=True)

    monkeypatch.setenv("COGNITO_REGION", REGION)
    monkeypatch.setenv("COGNITO_POOL_ID", pool_id)
    monkeypatch.setenv("COGNITO_CLIENT_ID", client_id)
    monkeypatch.syspath_prepend(str(EXAMPLE / "lambda"))
    for m in ("miniui", "app"):
        sys.modules.pop(m, None)
    import app  # noqa: F401  (the example's app.py)

    yield app
    for m in ("miniui", "app"):
        sys.modules.pop(m, None)


def event(path, method="GET", body=None, cookies=None):
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "body": body,
        "cookies": cookies or [],
        "isBase64Encoded": False,
    }


def test_miniui_full_session(miniui_app):
    app = miniui_app

    # anonymous -> login page, not the app
    resp = app.handler(event("/"), None)
    assert resp["statusCode"] == 401
    assert "Sign in" in resp["body"]

    # wrong password -> 401 with the error shown
    resp = app.handler(event("/login", "POST",
                             "email=me%40example.com&password=nope"), None)
    assert resp["statusCode"] == 401
    assert "wrong email or password" in resp["body"]

    # real login -> session cookie + redirect home
    resp = app.handler(event("/login", "POST",
                             "email=me%40example.com&password=CorrectHorse9!"), None)
    assert resp["statusCode"] == 303
    cookie = resp["cookies"][0].split(";")[0]
    assert cookie.startswith("miniui_session=")

    # signed in: static index served from the zip's static/
    resp = app.handler(event("/", cookies=[cookie]), None)
    assert resp["statusCode"] == 200
    assert "mini ui" in resp["body"]

    resp = app.handler(event("/app.js", cookies=[cookie]), None)
    assert resp["statusCode"] == 200
    assert "javascript" in resp["headers"]["Content-Type"]

    # authenticated API dispatch, with the user identity attached
    resp = app.handler(event("/api/echo", "POST", '{"message": "hi"}', [cookie]), None)
    assert resp["statusCode"] == 200
    assert '"you_sent": {"message": "hi"}' in resp["body"]
    assert "me@example.com" in resp["body"]

    # unknown api -> 404; api without auth -> login page
    assert app.handler(event("/api/nope", "POST", "{}", [cookie]), None)["statusCode"] == 404
    assert app.handler(event("/api/echo", "POST", "{}"), None)["statusCode"] == 401

    # path traversal out of static/ -> 404
    resp = app.handler(event("/../miniui.py", cookies=[cookie]), None)
    assert resp["statusCode"] == 404

    # logout kills the session
    resp = app.handler(event("/logout", cookies=[cookie]), None)
    assert resp["statusCode"] == 303
    assert "Max-Age=0" in resp["cookies"][0]


def test_create_function_retries_role_propagation(aws, zip_path, monkeypatch):
    """IAM roles take time to propagate to Lambda; the 'cannot be assumed'
    rejection must be retried, not fatal."""
    from botocore.exceptions import ClientError

    from GraphIaC.aws import lambda_func

    iam = aws.client("iam")
    role_arn = iam.create_role(
        RoleName="prop-role",
        AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
        '"Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}',
    )["Role"]["Arn"]

    class FlakyLambda:
        def __init__(self, real):
            self.real, self.failures = real, 2

        def create_function(self, **kw):
            if self.failures:
                self.failures -= 1
                raise ClientError(
                    {"Error": {"Code": "InvalidParameterValueException",
                               "Message": "The role defined for the function cannot be assumed by Lambda."}},
                    "CreateFunction",
                )
            return self.real.create_function(**kw)

        def __getattr__(self, name):
            return getattr(self.real, name)

    class FlakySession:
        def __init__(self, real):
            self.real = real
            self.flaky = FlakyLambda(real.client("lambda", region_name=REGION))

        def client(self, service, **kw):
            return self.flaky if service == "lambda" else self.real.client(service, **kw)

    monkeypatch.setattr(lambda_func.time, "sleep", lambda s: None)
    session = FlakySession(aws)
    lambda_func.lambda_create(session, "prop-fn", "python3.13", role_arn, "app.handler",
                              "d", 15, 128, True, zip_path, REGION)
    assert session.flaky.failures == 0  # both flakes consumed, then success
    assert aws.client("lambda", region_name=REGION).get_function(FunctionName="prop-fn")


def test_create_asserts_lambda_trust_on_imported_role(aws, zip_path, tmp_path):
    """A pre-existing role that only trusts the account (graphiac-deploy's
    shape) must gain Lambda trust BEFORE create_function — the edge that
    owns trust runs after nodes, too late."""
    import json

    import networkx as nx

    from GraphIaC.aws.iam_role import IAMRole

    iam = aws.client("iam")
    root_only = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::123456789012:root"},
                       "Action": "sts:AssumeRole"}],
    }
    arn = iam.create_role(RoleName="root-trust-role",
                          AssumeRolePolicyDocument=json.dumps(root_only))["Role"]["Arn"]

    G = nx.DiGraph()
    role = IAMRole(g_id="role", name="root-trust-role", arn=arn)
    fn = LambdaZipFile(g_id="fn", name="trust-fn", runtime="python3.13",
                       handler="app.handler", zip_file_path=zip_path, region=REGION)
    G.add_node("role", data=role)
    G.add_node("fn", data=fn)
    G.add_edge("role", "fn", data=IAMRolePolicyLambdaEdge(role_g_id="role", node_g_id="fn"))

    fn.create(aws, G)  # node create, edge NOT yet applied — the real run order

    doc = iam.get_role(RoleName="root-trust-role")["Role"]["AssumeRolePolicyDocument"]
    if isinstance(doc, str):
        doc = json.loads(doc)
    services = [s.get("Principal", {}).get("Service") for s in doc["Statement"]]
    assert "lambda.amazonaws.com" in services
    aws.client("lambda", region_name=REGION).get_function(FunctionName="trust-fn")
