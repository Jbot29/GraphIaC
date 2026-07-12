"""miniui — a serverless web app in one Lambda, no framework, no deps.

Serves static assets bundled in the deployment zip, logs users in against
Cognito (the pool/client/region arrive as environment variables, wired by
GraphIaC's CognitoLambdaAuthEdge), and dispatches authenticated JSON calls
to the APIS dict in app.py. boto3 is already in the Lambda runtime, so the
zip contains nothing but your code.

Sessions: the Cognito access token rides in an HttpOnly Secure __Host-
cookie (SameSite=Strict), and every request is validated against Cognito
itself (get_user) — no local crypto, nothing to get wrong.

Security posture (this is a base to fork — know what it does and doesn't):
  - CSRF: state-changing APIs are POST-only; the session cookie is
    SameSite=Strict + __Host-, so a cross-site page cannot ride it.
  - Every response carries CSP/nosniff/frame-deny/HSTS (see _respond).
  - Logout revokes the token at Cognito, not just the cookie.
  - AUTHORIZATION IS ALL-OR-NOTHING: any signed-in user can call every
    API. Per-user / per-action authorization is the responsibility of the
    functions in APIS — they receive `user` (the email) for exactly this.
  - Brute force relies on Cognito's own throttling/lockout for
    USER_PASSWORD_AUTH; enable the pool's advanced security features.
"""

import base64
import html
import json
import logging
import mimetypes
import os
import urllib.parse

import boto3

logger = logging.getLogger("miniui")

REGION = os.environ.get("COGNITO_REGION")
CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
# __Host- prefix hardens the cookie (browser enforces Secure + Path=/ + no
# Domain) — prevents a sibling/subdomain from planting a session cookie.
COOKIE = "__Host-miniui_session"

_idp = boto3.client("cognito-idp", region_name=REGION) if REGION else None

# Loosened for inline assets only where the bundled pages actually need it
# (the editor ships an inline <script>; login/editor use inline <style>).
# If your own static/ has no inline script, drop 'unsafe-inline' from
# script-src for real XSS protection.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=63072000",
    "Content-Security-Policy": _CSP,
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

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


def _login_page(error=""):
    """Render the login page. Always HTML-escapes `error` — a footgun
    otherwise, the day someone passes user-controlled text."""
    return LOGIN_PAGE.format(error=html.escape(error))


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
        # security headers first, then content-type, then per-call overrides
        "headers": {**SECURITY_HEADERS, "Content-Type": content_type, **(headers or {})},
        "body": body,
    }
    if cookies:
        resp["cookies"] = cookies
    return resp


def _redirect(to, cookies=None):
    return _respond(303, "", headers={"Location": to}, cookies=cookies)


def _session_cookie(token):
    return (f"{COOKIE}={token}; HttpOnly; Secure; SameSite=Strict; "
            f"Path=/; Max-Age=3600")


def _cleared_cookie():
    return f"{COOKIE}=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0"


def _login(email, password):
    """An access token, or None if Cognito says no. Never raises — an auth
    boundary must not 500 on hostile input."""
    if not email or not password:
        return None
    try:
        resp = _idp.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
    except _idp.exceptions.ClientError:
        return None  # wrong credentials, unknown user, throttled — all normal
    except Exception:  # ParamValidationError, network, misconfig — don't 500
        logger.exception("login failed unexpectedly")
        return None
    if resp.get("ChallengeName"):
        # user is mid-challenge (NEW_PASSWORD_REQUIRED / MFA) — miniui doesn't
        # drive challenges; set a permanent password (see the example README)
        logger.info("login blocked by challenge: %s", resp["ChallengeName"])
        return None
    return resp.get("AuthenticationResult", {}).get("AccessToken")


def _logout(token):
    """Revoke the token at Cognito (best effort) — clearing the cookie alone
    leaves a stolen token valid until expiry."""
    if not token:
        return
    try:
        _idp.global_sign_out(AccessToken=token)
    except Exception:
        pass  # already expired/invalid, or throttled — the cookie clears anyway


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
    # realpath + commonpath: neutralizes ../, resolves symlinks, and (unlike a
    # bare startswith) won't let a sibling like static.bak/ escape the root
    root = os.path.realpath(STATIC_DIR)
    target = os.path.realpath(os.path.join(STATIC_DIR, name))
    if os.path.commonpath([target, root]) != root or not os.path.isfile(target):
        return _respond(404, "not found", "text/plain")
    ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
    with open(target, "rb") as f:
        data = f.read()
    if ctype.startswith(("text/", "application/javascript", "application/json")):
        return _respond(200, data.decode("utf-8"), f"{ctype}; charset=utf-8")
    return {
        "statusCode": 200,
        "headers": {**SECURITY_HEADERS, "Content-Type": ctype},
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
            return _respond(401, _login_page("wrong email or password"))
        return _redirect("/", cookies=[_session_cookie(token)])

    if req["path"] == "/logout":
        _logout(req["cookies"].get(COOKIE))
        return _redirect("/", cookies=[_cleared_cookie()])

    user = _whoami(req["cookies"].get(COOKIE))
    if not user:
        return _respond(401, _login_page())

    if req["path"].startswith("/api/"):
        # POST-only: with SameSite=Strict this closes CSRF without tokens —
        # a cross-site GET navigation cannot invoke a state-changing API
        if req["method"] != "POST":
            return _respond(405, json.dumps({"error": "POST required"}),
                            "application/json", headers={"Allow": "POST"})
        name = req["path"][len("/api/"):]
        fn = apis.get(name)
        if not fn:
            return _respond(404, json.dumps({"error": f"no api named {name}"}), "application/json")
        try:
            payload = json.loads(req["body"]) if req["body"] else {}
        except json.JSONDecodeError:
            return _respond(400, json.dumps({"error": "body must be JSON"}), "application/json")
        try:
            result = fn(payload, user)
            return _respond(200, json.dumps(result), "application/json")
        except Exception:  # an API bug must not leak internals or crash the invocation
            logger.exception("api '%s' failed", name)
            return _respond(500, json.dumps({"error": "internal error"}), "application/json")

    return _static(req["path"])
