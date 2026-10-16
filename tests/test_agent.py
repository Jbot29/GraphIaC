"""The agent: prompt construction, the parser check, the retry, and the
Api route. No network — a scripted fake stands in for the Anthropic client.
"""

import json
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from GraphIaC import agent as agent_mod
from GraphIaC.agent import Agent, catalog, system_prompt, user_prompt
from GraphIaC.dsl_registry import build_registry
from GraphIaC.server import Api

SRC = 'domain = "yourco.com"\n\nhz : HostedZone(domain_name: domain)\n'
GOOD = SRC + "bucket : S3Bucket\n\n? private(bucket)\n"
BAD = SRC + "bucket : NoSuchType\n"


class FakeClient:
    """Returns the scripted replies in order; records every request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if reply == "REFUSAL":
            return SimpleNamespace(stop_reason="refusal", content=[])
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


# ---------------------------------------------------------------- prompt
def test_catalog_covers_every_type_the_parser_accepts():
    reg = build_registry()
    text = catalog(reg)
    for node in reg["nodes"]:
        assert node in text
    for edge, info in reg["edges"].items():
        assert edge in text
        assert f"{info['source']['type']} -> {info['dest']['type']}" in text
    for pred in reg["predicates"]:
        assert f"? {pred}(" in text


def test_catalog_marks_name_fields_and_subclasses():
    text = catalog(build_registry())
    assert "name field: bucket_name (defaults to the label)" in text
    assert "DeployRole  (isa IAMRole" in text


def test_system_prompt_is_stable_between_calls():
    # identical bytes every time, or the prompt cache never hits
    assert system_prompt() == system_prompt()


def test_user_prompt_lists_other_files_and_context():
    text = user_prompt(SRC, "add a bucket", file="site.giac", files=["site.giac", "api/api.giac"],
                       context="⊘ CloudFrontDistribution cf BLOCKED",
                       history=[{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}])
    assert "  api/api.giac" in text and "  site.giac" not in text
    assert "The open file (site.giac)" in text
    assert "BLOCKED" in text
    assert "Person (earlier): hi" in text and "You (earlier): hello" in text
    assert text.rstrip().endswith("Request: add a bucket")


# ------------------------------------------------------------------ ask
def test_valid_source_is_accepted_first_time():
    client = FakeClient({"reply": "Added a private bucket.", "source": GOOD})
    out = Agent(client=client).ask(SRC, "add a private bucket", file="site.giac")
    assert out == {"reply": "Added a private bucket.", "source": GOOD, "errors": [], "attempts": 1}
    call = client.calls[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"]["format"]["schema"] == agent_mod.OUTPUT_SCHEMA
    assert call["messages"][0]["content"].endswith("Request: add a private bucket")


def test_unparseable_source_is_sent_back_once_with_the_parser_errors():
    client = FakeClient({"reply": "try", "source": BAD}, {"reply": "fixed", "source": GOOD})
    out = Agent(client=client).ask(SRC, "add a bucket")
    assert out["attempts"] == 2
    assert out["source"] == GOOD and out["errors"] == []
    retry = client.calls[1]["messages"]
    assert retry[1]["role"] == "assistant"  # the bad attempt stays in the transcript
    assert "does not parse" in retry[2]["content"]
    assert "NoSuchType" in retry[2]["content"]  # the parser's own words, verbatim


def test_second_failure_returns_source_with_errors_for_the_person_to_see():
    client = FakeClient({"reply": "a", "source": BAD}, {"reply": "b", "source": BAD})
    out = Agent(client=client).ask(SRC, "add a bucket")
    assert out["attempts"] == 2
    assert out["source"] == BAD
    assert out["errors"] and out["errors"][0]["line"] == 4


def test_a_question_returns_prose_and_no_source():
    client = FakeClient({"reply": "cf waits on the certificate being ISSUED.", "source": None})
    out = Agent(client=client).ask(SRC, "why is cf blocked?")
    assert out["source"] is None and out["errors"] == [] and out["attempts"] == 1


def test_non_json_and_refusals_degrade_to_prose():
    out = Agent(client=FakeClient("not json at all")).ask(SRC, "x")
    assert out["source"] is None and out["reply"] == "not json at all"
    out = Agent(client=FakeClient("REFUSAL")).ask(SRC, "x")
    assert out["source"] is None and "declined" in out["reply"]


# ------------------------------------------------------------------ Api
@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    (tmp_path / "site.giac").write_text(SRC)
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "api.giac").write_text("api : ApiSite\n")
    with mock_aws():
        yield Api(boto3.session.Session(region_name="us-east-1"), str(tmp_path / "site.giac"))


def test_files_reports_agent_availability(api, monkeypatch):
    monkeypatch.setattr(agent_mod, "available", lambda: False)
    assert api.get_files()[1]["agent"] is False
    api.agent = Agent(client=FakeClient())
    assert api.get_files()[1]["agent"] is True


def test_post_agent_without_credentials_is_503(api, monkeypatch):
    monkeypatch.setattr(agent_mod, "available", lambda: False)
    status, data = api.post_agent({"source": SRC, "request": "add a bucket"})
    assert status == 503 and "ANTHROPIC_API_KEY" in data["error"]


def test_post_agent_requires_source_and_request(api):
    api.agent = Agent(client=FakeClient())
    assert api.post_agent({"source": SRC})[0] == 400
    assert api.post_agent({"request": "x"})[0] == 400


def test_post_agent_passes_workspace_and_context_through(api):
    client = FakeClient({"reply": "ok", "source": GOOD})
    api.agent = Agent(client=client)
    status, data = api.post_agent({
        "source": SRC, "request": "add a bucket", "file": "site.giac",
        "context": "✓ no changes", "history": [{"role": "user", "text": "earlier"}],
    })
    assert status == 200 and data["source"] == GOOD
    prompt = client.calls[0]["messages"][0]["content"]
    assert "  api/api.giac" in prompt and "The open file (site.giac)" in prompt
    assert "✓ no changes" in prompt and "Person (earlier): earlier" in prompt


def test_post_agent_api_failure_is_502_not_a_crash(api):
    class Boom:
        beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("down"))))

    api.agent = Agent(client=Boom())
    status, data = api.post_agent({"source": SRC, "request": "x"})
    assert status == 502 and "down" in data["error"]


def test_available_is_false_without_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert agent_mod.available() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert agent_mod.available() is True
