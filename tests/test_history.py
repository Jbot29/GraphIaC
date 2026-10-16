"""Source history: snapshots on save, agent accept, and run, kept in the
per-script state DB. Rollback is restore + plan + run — nothing here
touches the engine beyond recording.
"""

import sqlite3

import boto3
import pytest
from moto import mock_aws

from GraphIaC.db import create_tables, get_version, list_versions, record_source
from GraphIaC.main import summarize_ops
from GraphIaC.server import Api

V1 = "demo-bucket : S3Bucket\n"
V2 = V1 + "\n? private(demo-bucket)\n"


# ------------------------------------------------------------------ db
@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    create_tables(c)
    yield c
    c.close()


def test_record_list_get(conn):
    v = record_source(conn, V1, "save")
    assert v["id"] == 1 and v["event"] == "save" and v["message"] is None and len(v["sha"]) == 16
    v2 = record_source(conn, V2, "agent", "add a guard")
    assert v2["id"] == 2
    versions = list_versions(conn)
    assert [x["id"] for x in versions] == [2, 1]  # newest first
    assert versions[0]["message"] == "add a guard" and versions[0]["bytes"] == len(V2)
    assert "source" not in versions[0]
    assert get_version(conn, 1)["source"] == V1
    assert get_version(conn, 99) is None


def test_unchanged_save_and_agent_are_not_duplicated(conn):
    assert record_source(conn, V1, "save") is not None
    assert record_source(conn, V1, "save") is None
    assert record_source(conn, V1, "agent", "noop") is None
    assert record_source(conn, V2, "save") is not None  # changed text records
    assert len(list_versions(conn)) == 2


def test_runs_are_always_recorded(conn):
    record_source(conn, V1, "save")
    assert record_source(conn, V1, "run", "+1")["event"] == "run"
    assert record_source(conn, V1, "run", "no changes")["id"] == 3


def test_unknown_event_is_rejected(conn):
    with pytest.raises(ValueError):
        record_source(conn, V1, "commit")


def test_older_db_grows_the_table_on_open(tmp_path):
    """A state DB written before history existed must keep working."""
    old = sqlite3.connect(str(tmp_path / "old.db"))
    old.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, g_id TEXT UNIQUE, type TEXT, data JSON)")
    old.commit()
    create_tables(old)
    assert list_versions(old) == []
    old.close()


def test_summarize_ops():
    assert summarize_ops(None) == "run failed"
    assert summarize_ops([]) == "no changes"


# ----------------------------------------------------------------- Api
@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    (tmp_path / "demo.giac").write_text(V1)
    with mock_aws():
        yield Api(boto3.session.Session(region_name="us-east-1"), str(tmp_path / "demo.giac"))


def test_save_records_a_version_once_per_change(api):
    status, data = api.post_source({"source": V1})
    assert status == 200 and data["version"]["id"] == 1 and data["version"]["event"] == "save"
    status, data = api.post_source({"source": V1})
    assert data["version"] is None  # same text, no new row
    status, data = api.post_source({"source": V2})
    assert data["version"]["id"] == 2

    status, data = api.get_history()
    assert status == 200 and [v["id"] for v in data["versions"]] == [2, 1]
    assert data["file"] == "demo.giac"


def test_agent_accept_records_with_the_request_as_message(api):
    status, data = api.post_snapshot({"source": V2, "event": "agent", "message": "add a guard"})
    assert status == 200
    assert data["version"]["event"] == "agent" and data["version"]["message"] == "add a guard"
    assert api.get_history(version_id=data["version"]["id"])[1]["version"]["source"] == V2


def test_snapshot_rejects_bad_events_and_files(api):
    assert api.post_snapshot({"source": V1, "event": "run"})[0] == 400  # runs come from run
    assert api.post_snapshot({"source": V1, "event": "commit"})[0] == 400
    assert api.post_snapshot({"event": "agent"})[0] == 400
    assert api.post_snapshot({"source": V1, "event": "agent", "file": "../x.giac"})[0] == 400


def test_run_records_the_file_as_applied(api):
    status, data = api.post_run({"source": V1})
    assert status == 200
    assert data["version"]["event"] == "run" and data["version"]["message"] == "+1"
    status, data = api.post_run({"source": V1})
    assert data["version"]["message"] == "no changes"  # runs record even when identical
    versions = api.get_history()[1]["versions"]
    assert [v["event"] for v in versions] == ["run", "run"]


def test_history_get_one_and_missing(api):
    api.post_source({"source": V1})
    status, data = api.get_history(version_id=1)
    assert status == 200 and data["version"]["source"] == V1
    assert api.get_history(version_id=42)[0] == 404
    assert api.get_history(file="../evil.giac")[0] == 400
    assert api.get_history(file="never-saved.giac")[1]["versions"] == []


def test_rollback_is_restore_then_run(api):
    """The whole rollback story: run v1, run v2, restore v1's text, run — the
    planner computes the way back."""
    api.post_run({"source": V1})
    bucket_v2 = V1 + "second-bucket : S3Bucket\n"
    api.post_run({"source": bucket_v2})
    api.session.client("s3").head_bucket(Bucket="second-bucket")

    first_run = [v for v in api.get_history()[1]["versions"] if v["message"] == "+1"][-1]
    restored = api.get_history(version_id=first_run["id"])[1]["version"]["source"]
    assert restored == V1
    status, data = api.post_run({"source": restored})
    assert [o["op"] for o in data["applied"]] == ["delete"]
    assert data["version"]["message"] == "-1"


# ------------------------------------------------------------ S3 state
@pytest.fixture
def s3_api(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    (tmp_path / "demo.giac").write_text(V1)
    with mock_aws():
        session = boto3.session.Session(region_name="us-east-1")
        session.client("s3").create_bucket(Bucket="ui-state")
        yield Api(session, str(tmp_path / "demo.giac"), state_url="s3://ui-state/team")


def test_s3_mode_records_runs_only(s3_api, tmp_path):
    status, data = s3_api.post_source({"source": V1})
    assert status == 200 and data["version"] is None
    status, data = s3_api.post_snapshot({"source": V2, "event": "agent", "message": "x"})
    assert status == 200 and data["version"] is None and "run snapshots are" in data["note"]
    assert not (tmp_path / "demo.db").exists()  # nothing leaked next to the script

    status, data = s3_api.post_run({"source": V1})
    assert data["version"]["event"] == "run"

    # the snapshot travelled with the state: a fresh Api on another machine sees it
    fresh = Api(s3_api.session, s3_api.infra_path, state_url="s3://ui-state/team")
    versions = fresh.get_history()[1]["versions"]
    assert len(versions) == 1 and versions[0]["event"] == "run"
    assert fresh.get_history(version_id=versions[0]["id"])[1]["version"]["source"] == V1
