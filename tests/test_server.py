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


# ---------------------------------------------------------------------
# serve with --state: the control panel against S3-backed state
# ---------------------------------------------------------------------
@pytest.fixture
def s3_api(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    infra = tmp_path / "demo.giac"
    infra.write_text(SRC)
    with mock_aws():
        session = boto3.session.Session(region_name="us-east-1")
        session.client("s3").create_bucket(Bucket="ui-state")
        yield Api(session, str(infra), state_url="s3://ui-state/team")


def test_s3_api_source_reports_state_url(s3_api):
    status, data = s3_api.get_source()
    assert status == 200
    assert data["state"] == "s3://ui-state/team"


def test_s3_api_run_publishes_and_plan_reads_back(s3_api, tmp_path):
    status, data = s3_api.post_run({"source": SRC})
    assert status == 200
    assert data["applied"][0]["op"] == "create"

    # the state landed in S3, not next to the infra file
    s3_api.session.client("s3").head_object(Bucket="ui-state", Key="team/demo.db")
    assert not (tmp_path / "demo.db").exists()

    # a completely fresh Api (new machine) sees converged state
    fresh = Api(s3_api.session, s3_api.infra_path, state_url="s3://ui-state/team")
    status, data = fresh.post_plan({"source": SRC})
    assert status == 200
    assert data["ops"] == []

    # and the lock is gone after the run
    from GraphIaC.state import S3State

    S3State(s3_api.session, "s3://ui-state/team", "demo.db").acquire("check")


def test_s3_api_run_locked_returns_423(s3_api):
    from GraphIaC.state import S3State

    other = S3State(s3_api.session, "s3://ui-state/team", "demo.db")
    other.acquire(operation="run")  # someone else's run in flight

    status, data = s3_api.post_run({"source": SRC})
    assert status == 423
    assert "locked" in data["error"].lower()
    assert "unlock" in data["error"]

    # reads are unaffected by the S3 lock
    status, data = s3_api.post_plan({"source": SRC})
    assert status == 200
    other.release()
