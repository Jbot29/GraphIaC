"""Tests for the backend Api (transport-agnostic core of server.py).

The Api takes dicts and returns (status, dict) — no HTTP needed to test
the logic. moto supplies AWS.
"""

import boto3
import pytest
from moto import mock_aws

from GraphIaC.server import Api

SRC = "demo-bucket : S3Bucket\n"


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    infra = tmp_path / "demo.giac"
    infra.write_text(SRC)
    with mock_aws():
        session = boto3.session.Session(region_name="us-east-1")
        yield Api(session, str(infra))


def test_source_roundtrip(api):
    status, data = api.get_source()
    assert status == 200
    assert data["source"] == SRC

    status, data = api.post_source({"source": SRC + "# edited\n"})
    assert status == 200 and data["saved"]
    assert api.get_source()[1]["source"].endswith("# edited\n")


def test_source_requires_body(api):
    status, data = api.post_source({})
    assert status == 400


def test_plan_run_converge(api):
    status, data = api.post_plan({"source": SRC})
    assert status == 200
    assert data["ops"] == [{"op": "create", "type": "S3Bucket", "label": "demo-bucket"}]

    status, data = api.post_run({"source": SRC})
    assert status == 200
    assert data["applied"][0]["op"] == "create"
    api.session.client("s3").head_bucket(Bucket="demo-bucket")  # really there

    status, data = api.post_plan({"source": SRC})
    assert status == 200
    assert data["ops"] == []


def test_parse_errors_are_400(api):
    status, data = api.post_plan({"source": "what is this"})
    assert status == 400
    assert data["errors"][0]["line"] == 1


def test_blocked_ops_carry_reasons(api):
    src = (
        'hz   : HostedZone(domain_name: "x.co")\n'
        'cert : ACMCertificate(domain_name: "x.co")\n'
        'cf   : CloudFrontDistribution(domain_name: "x.co", cert_arn: cert.arn)\n'
        "cert -> hz\n"
    )
    status, data = api.post_plan({"source": src})
    assert status == 200
    blocked = [o for o in data["ops"] if o["op"] == "blocked"]
    assert blocked == [
        {
            "op": "blocked",
            "type": "CloudFrontDistribution",
            "label": "cf",
            "reason": 'waiting on "cert" — not created yet',
        }
    ]


def test_verify_returns_checks(api):
    api.post_run({"source": SRC})
    status, data = api.post_verify({"source": SRC})
    assert status == 200
    assert any(c["name"] == "Public access block" and c["passed"] for c in data["checks"])
    # a bare bucket has no OAC policy — that check correctly fails
    assert data["failed"] == 1
    assert any(c["name"] == "Bucket policy" and not c["passed"] for c in data["checks"])


def test_busy_lock_returns_409(api):
    api.lock.acquire()
    try:
        status, data = api.post_plan({"source": SRC})
        assert status == 409
    finally:
        api.lock.release()
