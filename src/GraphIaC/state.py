"""S3-backed state: the SQLite DB lives in a bucket, with locking.

No DynamoDB. S3's conditional writes (2024) make a plain lockfile honest:

    acquire:  PUT <db>.lock with If-None-Match:* — atomic create-if-absent;
              412 means someone else holds it (their identity is in the body)
    work:     GET <db> to a local temp file; the engine runs on local SQLite
    publish:  PUT <db> back with If-Match on the ETag we downloaded — CAS
              belt-and-suspenders even while holding the lock
    release:  DELETE <db>.lock

This is the same protocol Terraform moved to (1.10+), retiring its
DynamoDB lock tables.

Philosophy: locks are never stolen automatically. A held lock reports
who/when/what and stops; `unlock` is the explicit human override. Only
`run` locks — `plan`/`verify` read the latest state lock-free and
tolerate the tiny staleness window. Turn versioning ON for the state
bucket (see examples/team-state/): it is free time travel.
"""

import getpass
import json
import os
import socket
import tempfile
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from .logs import setup_logger

logger = setup_logger()


class LockHeld(Exception):
    def __init__(self, info):
        self.info = info or {}
        who = self.info.get("holder", "unknown")
        since = self.info.get("acquired", "unknown time")
        op = self.info.get("operation", "?")
        super().__init__(
            f"state is locked by {who} (op: {op}, since {since}) — "
            f"if that run is dead, release it with the `unlock` command"
        )


class S3State:
    """One infra file's state in S3: s3://bucket[/prefix] + <name>.db."""

    def __init__(self, session, url, db_name):
        if not url.startswith("s3://"):
            raise ValueError(f"--state wants s3://bucket[/prefix], got {url}")
        rest = url[len("s3://"):]
        self.bucket, _, prefix = rest.partition("/")
        prefix = prefix.strip("/")
        if prefix.endswith(".db"):
            self.db_key = prefix
        else:
            self.db_key = f"{prefix}/{db_name}" if prefix else db_name
        self.lock_key = self.db_key + ".lock"
        self.s3 = session.client("s3")
        self.session = session
        self._etag = None
        self._local = None

    # ---- lock ----
    def _holder(self):
        try:
            arn = self.session.client("sts").get_caller_identity()["Arn"]
        except ClientError:
            arn = getpass.getuser()
        return arn

    def acquire(self, operation):
        body = json.dumps({
            "holder": self._holder(),
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "operation": operation,
            "acquired": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        try:
            self.s3.put_object(Bucket=self.bucket, Key=self.lock_key,
                               Body=body.encode(), IfNoneMatch="*")
            logger.info(f"Locked s3://{self.bucket}/{self.lock_key}")
        except ClientError as e:
            if e.response["Error"]["Code"] in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise LockHeld(self.read_lock()) from None
            raise

    def read_lock(self):
        """The current lock's contents, or None when unlocked."""
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=self.lock_key)["Body"].read()
            return json.loads(body)
        except ClientError:
            return None
        except json.JSONDecodeError:
            return {}

    def release(self):
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=self.lock_key)
            logger.info("Lock released")
        except ClientError as e:
            logger.error(f"Failed to release lock: {e}")

    def force_unlock(self):
        """The explicit human override for a dead run's lock."""
        info = self.read_lock()
        if info is None:
            logger.info("No lock to release")
            return None
        self.release()
        logger.info(f"Force-released lock held by {info.get('holder', 'unknown')} "
                    f"(since {info.get('acquired', '?')})")
        return info

    # ---- state ----
    def fetch(self):
        """Download the DB to a local temp file (fresh file when none exists
        yet); returns the local path for sqlite3.connect."""
        fd, self._local = tempfile.mkstemp(prefix="graphiac-state-", suffix=".db")
        os.close(fd)
        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=self.db_key)
            with open(self._local, "wb") as f:
                f.write(resp["Body"].read())
            self._etag = resp["ETag"]
            logger.info(f"Fetched state from s3://{self.bucket}/{self.db_key}")
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("NoSuchKey", "404"):
                raise
            os.unlink(self._local)  # sqlite will create it
            self._etag = None
            logger.info(f"No state at s3://{self.bucket}/{self.db_key} yet — starting fresh")
        return self._local

    def publish(self):
        """Upload the DB back, CAS-guarded by the ETag we fetched."""
        if not self._local or not os.path.exists(self._local):
            return
        with open(self._local, "rb") as f:
            data = f.read()
        kwargs = {"Bucket": self.bucket, "Key": self.db_key, "Body": data}
        if self._etag:
            kwargs["IfMatch"] = self._etag
        else:
            kwargs["IfNoneMatch"] = "*"  # first publish: nobody else may have raced one in
        try:
            resp = self.s3.put_object(**kwargs)
            self._etag = resp["ETag"]
            logger.info(f"Published state to s3://{self.bucket}/{self.db_key}")
        except ClientError as e:
            if e.response["Error"]["Code"] in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise RuntimeError(
                    f"state at s3://{self.bucket}/{self.db_key} changed underneath this run "
                    f"(was the lock force-released?) — local copy kept at {self._local}"
                ) from None
            raise

    def cleanup(self):
        if self._local and os.path.exists(self._local):
            os.unlink(self._local)
