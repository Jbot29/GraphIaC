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
# workspace: every .giac under the served directory, switchable per
# request via the `file` param; each script keeps its own state DB
# ---------------------------------------------------------------------
NEWS_SRC = "news-bucket : S3Bucket\n"


@pytest.fixture
def ws_api(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    (tmp_path / "main.giac").write_text(SRC)
    news = tmp_path / "newsletter"
    news.mkdir()
    (news / "newsletter.giac").write_text(NEWS_SRC)
    (news / "lambda-code").mkdir()  # non-.giac clutter stays invisible
    hidden = tmp_path / ".checkpoints"
    hidden.mkdir()
    (hidden / "old.giac").write_text(SRC)  # dot-dirs stay invisible too
    with mock_aws():
        session = boto3.session.Session(region_name="us-east-1")
        yield Api(session, str(tmp_path / "main.giac"))


def test_files_lists_workspace(ws_api):
    status, data = ws_api.get_files()
    assert status == 200
    assert data["files"] == ["main.giac", "newsletter/newsletter.giac"]
    assert data["default"] == "main.giac"


def test_source_by_file_param(ws_api):
    status, data = ws_api.get_source("newsletter/newsletter.giac")
    assert status == 200
    assert data["source"] == NEWS_SRC
    assert data["file"] == "newsletter/newsletter.giac"
    # no file param -> the file serve was started with
    assert ws_api.get_source()[1]["source"] == SRC


def test_file_param_rejects_escapes(ws_api):
    for bad in ("../evil.giac", "/etc/evil.giac", "main.py", "newsletter/../../evil.giac"):
        assert ws_api.get_source(bad)[0] == 400
        assert ws_api.post_source({"source": "", "file": bad})[0] == 400
        assert ws_api.post_plan({"source": SRC, "file": bad})[0] == 400


def test_per_file_state_db(ws_api, tmp_path):
    status, data = ws_api.post_run({"source": NEWS_SRC, "file": "newsletter/newsletter.giac"})
    assert status == 200
    # state lands next to the script it belongs to
    assert (tmp_path / "newsletter" / "newsletter.db").exists()
    assert not (tmp_path / "main.db").exists()
    # newsletter converged; main plans against its own (empty) state
    assert ws_api.post_plan({"source": NEWS_SRC, "file": "newsletter/newsletter.giac"})[1]["ops"] == []
    assert ws_api.post_plan({"source": SRC})[1]["ops"][0]["op"] == "create"


def test_new_script_in_new_subdir(ws_api, tmp_path):
    status, data = ws_api.post_source({"source": NEWS_SRC, "file": "billing/billing.giac"})
    assert status == 200 and data["saved"]
    assert (tmp_path / "billing" / "billing.giac").read_text() == NEWS_SRC
    assert "billing/billing.giac" in ws_api.get_files()[1]["files"]


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


def test_s3_state_key_includes_subdir(s3_api, tmp_path):
    """Same-named scripts in different subdirectories must not share a
    state object: the relative path is part of the S3 key."""
    (tmp_path / "newsletter").mkdir()
    (tmp_path / "newsletter" / "newsletter.giac").write_text(NEWS_SRC)

    status, data = s3_api.post_run({"source": NEWS_SRC, "file": "newsletter/newsletter.giac"})
    assert status == 200
    s3_api.session.client("s3").head_object(Bucket="ui-state", Key="team/newsletter/newsletter.db")
    assert not (tmp_path / "newsletter" / "newsletter.db").exists()


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
