"""miniui — a serverless web app in one Lambda, no framework, no deps.

Serves static assets bundled in the deployment zip, logs users in against
Cognito (the pool/client/region arrive as environment variables, wired by
GraphIaC's CognitoLambdaAuthEdge), and dispatches authenticated JSON calls
to the APIS dict in app.py. boto3 is already in the Lambda runtime, so the
zip contains nothing but your code.

Sessions: the Cognito access token rides in an HttpOnly Secure cookie, and
every request is validated against Cognito itself (get_user) — no local
crypto, nothing to get wrong.
"""

import base64
import json
import mimetypes
import os
import urllib.parse

import boto3

REGION = os.environ.get("COGNITO_REGION")
CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
COOKIE = "miniui_session"

_idp = boto3.client("cognito-idp", region_name=REGION) if REGION else None

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in</title>
<style>
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
         background:#f7f2e6; color:#22304a;
         font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
  form {{ display:flex; flex-direction:column; gap:10px; width:280px;
          border:2px solid #22304a; background:#fbf8ef; padding:24px;
          box-shadow:3px 3px 0 rgba(34,48,74,0.18); }}
  h1 {{ font-size:15px; margin:0 0 6px; }}
  input {{ font:inherit; padding:8px; border:2px solid #22304a; background:#fff; }}
  button {{ font:inherit; padding:8px; cursor:pointer;
            background:#22304a; color:#f7f2e6; border:2px solid #22304a; }}
  .err {{ color:#c8451f; font-size:12px; min-height:1em; margin:0; }}
</style></head><body>
<form method="post" action="/login">
  <h1>Sign in</h1>
  <p class="err">{error}</p>
  <input name="email" type="email" placeholder="email" autofocus required>
  <input name="password" type="password" placeholder="password" required>
  <button type="submit">Sign in</button>
</form>
</body></html>"""


def _request(event):
    """Normalize a Lambda function-URL event (payload v2)."""
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8", "replace")
    cookies = {}
    for c in event.get("cookies") or []:
        k, _, v = c.partition("=")
        cookies[k] = v
    return {
        "method": event.get("requestContext", {}).get("http", {}).get("method", "GET"),
        "path": event.get("rawPath", "/"),
        "body": body,
        "cookies": cookies,
    }


def _respond(status, body, content_type="text/html; charset=utf-8", cookies=None, headers=None):
    resp = {
        "statusCode": status,
        "headers": {"Content-Type": content_type, **(headers or {})},
        "body": body,
    }
    if cookies:
        resp["cookies"] = cookies
    return resp


def _redirect(to, cookies=None):
    return _respond(303, "", headers={"Location": to}, cookies=cookies)


def _login(email, password):
    """An access token, or None if Cognito says no."""
    try:
        resp = _idp.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
        return resp.get("AuthenticationResult", {}).get("AccessToken")
    except _idp.exceptions.ClientError:
        return None


def _whoami(token):
    """The signed-in user's email, or None — validated by Cognito itself."""
    if not token:
        return None
    try:
        resp = _idp.get_user(AccessToken=token)
        attrs = {a["Name"]: a["Value"] for a in resp.get("UserAttributes", [])}
        return attrs.get("email", resp.get("Username"))
    except _idp.exceptions.ClientError:
        return None


def _static(path):
    name = "index.html" if path in ("/", "") else path.lstrip("/")
    target = os.path.normpath(os.path.join(STATIC_DIR, name))
    if not target.startswith(STATIC_DIR) or not os.path.isfile(target):
        return _respond(404, "not found", "text/plain")
    ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
    with open(target, "rb") as f:
        data = f.read()
    if ctype.startswith(("text/", "application/javascript", "application/json")):
        return _respond(200, data.decode("utf-8"), f"{ctype}; charset=utf-8")
    return {
        "statusCode": 200,
        "headers": {"Content-Type": ctype},
        "body": base64.b64encode(data).decode(),
        "isBase64Encoded": True,
    }


def serve(event, apis):
    """The whole app: login, logout, static assets, and /api/<name> dispatch."""
    if not (REGION and CLIENT_ID):
        return _respond(500, "COGNITO_* env not set — is the CognitoLambdaAuthEdge applied?",
                        "text/plain")
    req = _request(event)

    if req["path"] == "/login" and req["method"] == "POST":
        form = urllib.parse.parse_qs(req["body"])
        token = _login(form.get("email", [""])[0], form.get("password", [""])[0])
        if not token:
            return _respond(401, LOGIN_PAGE.format(error="wrong email or password"))
        cookie = f"{COOKIE}={token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=3600"
        return _redirect("/", cookies=[cookie])

    if req["path"] == "/logout":
        gone = f"{COOKIE}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0"
        return _redirect("/", cookies=[gone])

    user = _whoami(req["cookies"].get(COOKIE))
    if not user:
        return _respond(401, LOGIN_PAGE.format(error=""))

    if req["path"].startswith("/api/"):
        name = req["path"][len("/api/"):]
        fn = apis.get(name)
        if not fn:
            return _respond(404, json.dumps({"error": f"no api named {name}"}), "application/json")
        try:
            payload = json.loads(req["body"]) if req["body"] else {}
        except json.JSONDecodeError:
            return _respond(400, json.dumps({"error": "body must be JSON"}), "application/json")
        result = fn(payload, user)
        return _respond(200, json.dumps(result), "application/json")

    return _static(req["path"])
