import os
import sqlite3
import uuid

import boto3
import pytest


@pytest.fixture(scope="session")
def aws_session():
    profile = os.environ.get("AWS_PROFILE")
    if not profile:
        pytest.skip("AWS_PROFILE env var not set — skipping AWS tests")
    return boto3.Session(profile_name=profile)


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def unique_suffix():
    return uuid.uuid4().hex[:8]
