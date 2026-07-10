"""Guard evaluation against moto: every predicate in pass, fail, and
pending states — checked with code independent of the classes that
provision (the whole point)."""

import sqlite3

import boto3
import pytest
from moto import mock_aws

import GraphIaC
from GraphIaC import dsl, guards

REGION = "us-east-2"


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("MOTO_IAM_LOAD_MANAGED_POLICIES", "true")
    monkeypatch.setattr("moto.settings.ACM_VALIDATION_WAIT", 0)  # certs go ISSUED immediately
    with mock_aws():
        yield boto3.session.Session(region_name=REGION)


def results_for(session, src):
    res = dsl.parse(src)
    assert res["errors"] == [], res["errors"]
    return {r.label: r for r in guards.evaluate(session, res["graph"])}


def test_private_pending_then_pass_then_fail(aws):
    src = 'b : S3Bucket("guard-bucket")\n? private(b)\n'

    r = results_for(aws, src)["? private(b)"]
    assert r.status == "pending"  # bucket doesn't exist yet

    s3 = aws.client("s3")
    s3.create_bucket(Bucket="guard-bucket",
                     CreateBucketConfiguration={"LocationConstraint": REGION})
    s3.put_public_access_block(
        Bucket="guard-bucket",
        PublicAccessBlockConfiguration={"BlockPublicAcls": True, "IgnorePublicAcls": True,
                                        "BlockPublicPolicy": True, "RestrictPublicBuckets": True},
    )
    assert results_for(aws, src)["? private(b)"].status == "pass"

    # open it up -> fail (the vibe-coder scenario)
    s3.put_public_access_block(
        Bucket="guard-bucket",
        PublicAccessBlockConfiguration={"BlockPublicAcls": False, "IgnorePublicAcls": True,
                                        "BlockPublicPolicy": True, "RestrictPublicBuckets": True},
    )
    r = results_for(aws, src)["? private(b)"]
    assert r.status == "fail"
    assert "BlockPublicAcls" in r.message


def test_admin_only_signup_pass_and_fail(aws):
    idp = aws.client("cognito-idp", region_name=REGION)
    src = '(p)'  # placeholder to appease linters; real src below

    src = "p : CognitoUserPool(pool_name: \"guard-pool\")\n? admin-only-signup(p)\n"
    idp.create_user_pool(PoolName="guard-pool",
                         AdminCreateUserConfig={"AllowAdminCreateUserOnly": True})
    assert results_for(aws, src)["? admin-only-signup(p)"].status == "pass"

    src2 = "p : CognitoUserPool(pool_name: \"open-pool\")\n? admin-only-signup(p)\n"
    idp.create_user_pool(PoolName="open-pool",
                         AdminCreateUserConfig={"AllowAdminCreateUserOnly": False})
    assert results_for(aws, src2)["? admin-only-signup(p)"].status == "fail"


def test_authed_flags_public_url_without_auth(aws, tmp_path):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app.py", "def handler(e, c): return {}\n")
    zip_path = tmp_path / "d.zip"
    zip_path.write_bytes(buf.getvalue())

    iam = aws.client("iam")
    role_arn = iam.create_role(
        RoleName="g-role",
        AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
        '"Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}',
    )["Role"]["Arn"]
    lc = aws.client("lambda", region_name=REGION)
    lc.create_function(FunctionName="g-fn", Runtime="python3.13", Role=role_arn,
                       Handler="app.handler", Code={"ZipFile": zip_path.read_bytes()})

    src = ('f : LambdaZipFile(name: "g-fn", runtime: "python3.13", handler: "app.handler", '
           'zip_file_path: "x.zip")\n? authed(f)\n')

    # no URL at all -> nothing exposed -> pass
    assert results_for(aws, src)["? authed(f)"].status == "pass"

    # public URL, no auth env -> THE nightmare -> fail, loudly
    lc.create_function_url_config(FunctionName="g-fn", AuthType="NONE")
    r = results_for(aws, src)["? authed(f)"]
    assert r.status == "fail"
    assert "NO AUTH" in r.message

    # wire the env -> pass
    lc.update_function_configuration(
        FunctionName="g-fn",
        Environment={"Variables": {"COGNITO_POOL_ID": "x", "COGNITO_CLIENT_ID": "y",
                                   "COGNITO_REGION": REGION}},
    )
    lc.get_waiter("function_updated_v2").wait(FunctionName="g-fn")
    assert results_for(aws, src)["? authed(f)"].status == "pass"


def test_guards_report_counts_failures(aws):
    src = 'b : S3Bucket("no-such")\n? private(b)\n'
    res = dsl.parse(src)
    results = guards.evaluate(aws, res["graph"])
    assert guards.report(results) == 0  # pending is not a failure

    results[0].status = "fail"
    assert guards.report(results) == 1


def test_unresolved_ref_makes_guard_pending(aws):
    # cert not ISSUED -> cf blocked -> its fields unresolvable -> pending
    src = (
        'cert : ACMCertificate(domain_name: "x.co")\n'
        'b    : S3Bucket("pend-bucket")\n'
        'cf   : CloudFrontDistribution(domain_name: "x.co")\n'
        "cert -> cf\n"
        "cf -> b\n"
        "? https-only(cf)\n"
    )
    r = results_for(aws, src)["? https-only(cf)"]
    assert r.status == "pending"


def test_locked_to_full_arc_via_run(aws, tmp_path):
    """The static-site guards through the real engine: run the stack, then
    the relational guard passes; loosen the policy, it fails."""
    import json

    monkey_src = (
        'b  : S3Bucket("lock-bucket", region: "us-east-2")\n'
        'cert : ACMCertificate(domain_name: "l.co")\n'
        'cf : CloudFrontDistribution(domain_name: "l.co")\n'
        "cert -> cf\n"
        "cf -> b\n"
        "? locked-to(b, cf)\n"
        "? https-only(cf)\n"
    )
    aws.client("acm", region_name="us-east-1").request_certificate(
        DomainName="l.co", ValidationMethod="DNS")

    state = GraphIaC.init(aws, sqlite3.connect(str(tmp_path / "s.db")))
    res = dsl.parse(monkey_src)
    assert res["errors"] == []
    blocked = dsl.load_graph(state, res["graph"])
    assert blocked == []
    GraphIaC.run(state, blocked)

    # moto's list_distributions drops Aliases, so the guard's alias search
    # can't find the distribution (works on real AWS) — pin the id instead
    dist_id = aws.client("cloudfront").list_distributions()["DistributionList"]["Items"][0]["Id"]
    next(n for n in res["graph"]["nodes"] if n["g_id"] == "cf")["fields"]["distribution_id"] = dist_id

    results = {r.label: r for r in guards.evaluate(aws, res["graph"])}
    assert results["? locked-to(b, cf)"].status == "pass"
    assert results["? https-only(cf)"].status == "pass"

    # widen the bucket policy behind GraphIaC's back -> the guard catches it
    aws.client("s3").put_bucket_policy(Bucket="lock-bucket", Policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject",
                       "Resource": "arn:aws:s3:::lock-bucket/*"}],
    }))
    r = guards.evaluate(aws, res["graph"])
    by = {x.label: x for x in r}
    assert by["? locked-to(b, cf)"].status == "fail"
