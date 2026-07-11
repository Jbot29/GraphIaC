"""The hosted UI example, in-process against moto: login through miniui,
the GraphIaC editor served from the package, source stored in S3, and a
real plan/run through the HostedApi — GraphIaC hosting GraphIaC."""

import json
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

EXAMPLE = Path(__file__).parent.parent / "examples" / "graphiac-ui" / "lambda"
REGION = "us-east-2"


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        session = boto3.session.Session(region_name=REGION)
        session.client("s3").create_bucket(
            Bucket="hosted-state",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        idp = session.client("cognito-idp", region_name=REGION)
        pool_id = idp.create_user_pool(PoolName="hosted")["UserPool"]["Id"]
        client_id = idp.create_user_pool_client(
            UserPoolId=pool_id, ClientName="hosted",
            ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        )["UserPoolClient"]["ClientId"]
        idp.admin_create_user(UserPoolId=pool_id, Username="me@example.com",
                              MessageAction="SUPPRESS")
        idp.admin_set_user_password(UserPoolId=pool_id, Username="me@example.com",
                                    Password="CorrectHorse9!", Permanent=True)

        monkeypatch.setenv("COGNITO_REGION", REGION)
        monkeypatch.setenv("COGNITO_POOL_ID", pool_id)
        monkeypatch.setenv("COGNITO_CLIENT_ID", client_id)
        monkeypatch.setenv("GRAPHIAC_STATE", "s3://hosted-state/team")
        monkeypatch.syspath_prepend(str(EXAMPLE))
        for m in ("miniui", "handler"):
            sys.modules.pop(m, None)
        import handler  # the example's real code

        handler._api = None  # fresh HostedApi per test
        yield handler
        for m in ("miniui", "handler"):
            sys.modules.pop(m, None)


def event(path, method="GET", body=None, cookies=None):
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "body": body,
        "cookies": cookies or [],
        "isBase64Encoded": False,
    }


def login(app):
    resp = app.handler(event("/login", "POST",
                             "email=me%40example.com&password=CorrectHorse9!"), None)
    assert resp["statusCode"] == 303
    return resp["cookies"][0].split(";")[0]


def test_hosted_ui_full_arc(app):
    # anonymous -> login page, never the editor
    resp = app.handler(event("/"), None)
    assert resp["statusCode"] == 401
    assert "Sign in" in resp["body"]

    cookie = login(app)

    # the editor itself, served from the installed GraphIaC package
    resp = app.handler(event("/", cookies=[cookie]), None)
    assert resp["statusCode"] == 200
    assert "infrastructure you can read" in resp["body"]
    for asset in ("/graphiac.js", "/registry.js"):
        r = app.handler(event(asset, cookies=[cookie]), None)
        assert r["statusCode"] == 200 and "javascript" in r["headers"]["Content-Type"]

    # source: default template first, then saved to S3
    resp = app.handler(event("/api/source", cookies=[cookie]), None)
    data = json.loads(resp["body"])
    assert data["state"] == "s3://hosted-state/team"
    assert "my-bucket" in data["source"]  # the starter template

    src = 'demo : S3Bucket("hosted-demo-bucket", region: "us-east-2")\n? private(demo)\n'
    resp = app.handler(event("/api/source", "POST", json.dumps({"source": src}), [cookie]), None)
    assert json.loads(resp["body"])["saved"]
    # it landed in S3, next to the state
    import boto3 as b3

    obj = b3.client("s3").get_object(Bucket="hosted-state", Key="team/infra.giac")
    assert obj["Body"].read().decode() == src

    # plan then run, through the hosted Api with S3 state
    resp = app.handler(event("/api/plan", "POST", json.dumps({"source": src}), [cookie]), None)
    ops = json.loads(resp["body"])["ops"]
    assert [o["op"] for o in ops] == ["create"]

    resp = app.handler(event("/api/run", "POST", json.dumps({"source": src}), [cookie]), None)
    body = json.loads(resp["body"])
    assert body["applied"][0]["op"] == "create"
    assert body["guards"][0]["status"] == "pass"  # private(demo), independently checked
    b3.client("s3").head_bucket(Bucket="hosted-demo-bucket")  # it really exists

    # state was published: a second plan converges
    resp = app.handler(event("/api/plan", "POST", json.dumps({"source": src}), [cookie]), None)
    assert json.loads(resp["body"])["ops"] == []


def test_hosted_run_respects_the_s3_lock(app):
    from GraphIaC.state import S3State

    cookie = login(app)
    other = S3State(boto3.Session(), "s3://hosted-state/team", "infra.giac".replace(".giac", ".db"))
    other.acquire(operation="run")  # a CLI run somewhere holds the lock

    src = 'demo : S3Bucket("locked-demo", region: "us-east-2")\n'
    resp = app.handler(event("/api/run", "POST", json.dumps({"source": src}), [cookie]), None)
    assert resp["statusCode"] == 423
    assert "unlock" in json.loads(resp["body"])["error"]
    other.release()


def test_static_traversal_blocked(app):
    cookie = login(app)
    resp = app.handler(event("/../server.py", cookies=[cookie]), None)
    assert resp["statusCode"] == 404
