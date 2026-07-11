"""GraphIaC hosting GraphIaC: the editor UI served from a Lambda.

The whole server is user-space glue: miniui provides Cognito login (the
lambda-ui base), GraphIaC's own Api class provides plan/run/verify, and
this file wires them to a function-URL event. The infra source lives in
S3 next to the state (GRAPHIAC_STATE), so nothing depends on this
Lambda's disk.

Environment (wired by ui.giac):
    GRAPHIAC_STATE   s3://bucket[/prefix] — state AND source live here
    GRAPHIAC_SOURCE  source filename within the prefix (default infra.giac)
    COGNITO_*        wired by the CognitoLambdaAuthEdge
"""

import importlib.resources
import json
import os

import boto3
from botocore.exceptions import ClientError
from miniui import COOKIE, LOGIN_PAGE, _login, _request, _respond, _whoami

from GraphIaC.server import Api

STATE_URL = os.environ.get("GRAPHIAC_STATE", "")
SOURCE_NAME = os.environ.get("GRAPHIAC_SOURCE", "infra.giac")
WEB = importlib.resources.files("GraphIaC") / "web"

DEFAULT_SOURCE = """\
# Your infrastructure lives here — edited in this UI, stored in S3.
# Declare something and hit plan:
#
#   my-bucket : S3Bucket(region: "us-east-2")
#   ? private(my-bucket)
"""


class HostedApi(Api):
    """The Api with its source file in S3 instead of on disk — the only
    difference between the laptop server and the hosted one."""

    def __init__(self, session):
        super().__init__(session, "/tmp/" + SOURCE_NAME, state_url=STATE_URL)
        rest = STATE_URL[len("s3://"):]
        self.src_bucket, _, prefix = rest.partition("/")
        prefix = prefix.strip("/")
        self.src_key = f"{prefix}/{SOURCE_NAME}" if prefix else SOURCE_NAME
        self.s3 = session.client("s3")

    def get_source(self):
        try:
            body = self.s3.get_object(Bucket=self.src_bucket, Key=self.src_key)["Body"].read()
            source = body.decode("utf-8")
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("NoSuchKey", "404"):
                raise
            source = DEFAULT_SOURCE
        return 200, {"source": source, "path": f"s3://{self.src_bucket}/{self.src_key}",
                     "state": self.state_url}

    def post_source(self, body):
        if "source" not in body:
            return 400, {"error": 'missing "source"'}
        self.s3.put_object(Bucket=self.src_bucket, Key=self.src_key,
                           Body=body["source"].encode("utf-8"))
        return 200, {"saved": True, "path": f"s3://{self.src_bucket}/{self.src_key}"}


_api = None


def _get_api():
    global _api
    if _api is None:
        _api = HostedApi(boto3.Session())
    return _api


CONTENT_TYPES = {".html": "text/html; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8"}


def _static(path):
    name = "index.html" if path in ("/", "") else path.lstrip("/")
    if "/" in name or name.startswith("."):
        return _respond(404, "not found", "text/plain")
    target = WEB / name
    if not target.is_file():
        return _respond(404, "not found", "text/plain")
    ext = "." + name.rsplit(".", 1)[-1]
    return _respond(200, target.read_text(),
                    CONTENT_TYPES.get(ext, "application/octet-stream"))


def _json(pair):
    status, payload = pair
    return _respond(status, json.dumps(payload), "application/json")


def handler(event, context):
    if not STATE_URL:
        return _respond(500, "GRAPHIAC_STATE env not set — see ui.giac", "text/plain")
    req = _request(event)

    # ---- auth (miniui, unchanged) ----
    if req["path"] == "/login" and req["method"] == "POST":
        import urllib.parse

        form = urllib.parse.parse_qs(req["body"])
        token = _login(form.get("email", [""])[0], form.get("password", [""])[0])
        if not token:
            return _respond(401, LOGIN_PAGE.format(error="wrong email or password"))
        cookie = f"{COOKIE}={token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=3600"
        return {"statusCode": 303, "headers": {"Location": "/"}, "cookies": [cookie], "body": ""}
    if req["path"] == "/logout":
        gone = f"{COOKIE}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0"
        return {"statusCode": 303, "headers": {"Location": "/"}, "cookies": [gone], "body": ""}

    user = _whoami(req["cookies"].get(COOKIE))
    if not user:
        return _respond(401, LOGIN_PAGE.format(error=""))

    # ---- the GraphIaC API, exactly as served locally ----
    api = _get_api()
    if req["path"] == "/api/source":
        if req["method"] == "POST":
            return _json(api.post_source(json.loads(req["body"] or "{}")))
        return _json(api.get_source())
    if req["path"].startswith("/api/") and req["method"] == "POST":
        route = {"/api/plan": api.post_plan, "/api/run": api.post_run,
                 "/api/verify": api.post_verify}.get(req["path"])
        if not route:
            return _json((404, {"error": "not found"}))
        try:
            body = json.loads(req["body"] or "{}")
        except json.JSONDecodeError:
            return _json((400, {"error": "body must be JSON"}))
        return _json(route(body))

    # ---- the editor itself, straight from the GraphIaC package ----
    return _static(req["path"])
