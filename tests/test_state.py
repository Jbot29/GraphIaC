"""S3-backed state: the conditional-write lock protocol, ETag CAS on
publish, and the full two-machine round-trip through the real engine."""

import sqlite3

import boto3
import pytest
from moto import mock_aws

import GraphIaC
from GraphIaC import dsl
from GraphIaC.state import LockHeld, S3State

REGION = "us-east-2"


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        session = boto3.session.Session(region_name=REGION)
        session.client("s3").create_bucket(
            Bucket="state-bucket",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        yield session


def test_url_forms(aws):
    assert S3State(aws, "s3://b", "x.db").db_key == "x.db"
    assert S3State(aws, "s3://b/team/prod", "x.db").db_key == "team/prod/x.db"
    assert S3State(aws, "s3://b/exact/state.db", "x.db").db_key == "exact/state.db"
    with pytest.raises(ValueError):
        S3State(aws, "/local/path", "x.db")


def test_lock_is_exclusive_and_reports_holder(aws):
    a = S3State(aws, "s3://state-bucket/team", "site.db")
    b = S3State(aws, "s3://state-bucket/team", "site.db")

    a.acquire(operation="run")
    with pytest.raises(LockHeld) as exc:
        b.acquire(operation="run")
    assert "op: run" in str(exc.value)
    assert "unlock" in str(exc.value)  # tells the human the way out

    a.release()
    b.acquire(operation="run")  # now it's b's turn
    b.release()


def test_force_unlock_frees_a_dead_lock(aws):
    a = S3State(aws, "s3://state-bucket", "site.db")
    a.acquire(operation="run")  # and then the process "dies"

    b = S3State(aws, "s3://state-bucket", "site.db")
    info = b.force_unlock()
    assert info["operation"] == "run"
    b.acquire(operation="run")  # lock is free again
    b.release()

    assert b.force_unlock() is None  # unlocking nothing is a no-op


def test_fetch_publish_roundtrip_and_cas(aws):
    a = S3State(aws, "s3://state-bucket", "site.db")
    path = a.fetch()  # nothing there yet -> fresh
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()
    a.publish()
    a.cleanup()

    # another machine sees the data
    b = S3State(aws, "s3://state-bucket", "site.db")
    conn = sqlite3.connect(b.fetch())
    assert conn.execute("SELECT x FROM t").fetchone() == (42,)
    conn.close()

    # CAS: someone overwrites between b's fetch and publish -> loud failure
    c = S3State(aws, "s3://state-bucket", "site.db")
    cpath = c.fetch()
    conn = sqlite3.connect(cpath)
    conn.execute("INSERT INTO t VALUES (43)")  # actually change the bytes
    conn.commit()
    conn.close()
    c.publish()  # c wins the race
    with pytest.raises(RuntimeError, match="changed underneath"):
        b.publish()
    b.cleanup()
    c.cleanup()


def test_engine_roundtrip_two_machines(aws):
    """The point of all of it: machine A runs, machine B plans from a fresh
    checkout and sees a converged, empty plan."""
    src = 'demo : S3Bucket("state-demo-bucket", region: "us-east-2")\n'

    def machine(operation):
        backend = S3State(aws, "s3://state-bucket/team", "demo.db")
        if operation == "run":
            backend.acquire(operation)
        conn = sqlite3.connect(backend.fetch())
        state = GraphIaC.init(aws, conn)
        res = dsl.parse(src)
        assert res["errors"] == []
        blocked = dsl.load_graph(state, res["graph"])
        if operation == "run":
            GraphIaC.run(state, blocked)
            conn.close()
            backend.publish()
            backend.release()
            backend.cleanup()
            return None
        ops = GraphIaC.plan(state, blocked)
        conn.close()
        backend.cleanup()
        return ops

    machine("run")
    aws.client("s3").head_bucket(Bucket="state-demo-bucket")  # really created
    assert machine("plan") == []  # machine B: converged, no local .db anywhere


def test_run_lock_blocks_second_run(aws):
    a = S3State(aws, "s3://state-bucket/team", "demo.db")
    a.acquire(operation="run")
    b = S3State(aws, "s3://state-bucket/team", "demo.db")
    with pytest.raises(LockHeld):
        b.acquire(operation="run")
    # plan doesn't lock: b can still fetch freely while a holds the lock
    b.fetch()
    b.cleanup()
    a.release()
