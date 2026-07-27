"""A stand-in for your app: talks to Postgres, answers health checks.

Replace this with your Django/Rails/Express container. What's worth keeping
is how it gets its credentials.

`DATABASE_SECRET` is injected by ECS from Secrets Manager — the task
definition references the secret's ARN, and the agent fetches the value at
container start. It never passes through a build, an image layer, an
environment file, or your terminal. The permission to read it came from one
arrow in webapp.giac:

    web -> db : (role_g_id: task-role)
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("DATABASE_HOST", "")
NAME = os.environ.get("DATABASE_NAME", "app")
USER = os.environ.get("DATABASE_USER", "postgres")


def password():
    """RDS wrote the credentials; ECS handed us the whole secret as JSON."""
    raw = os.environ.get("DATABASE_SECRET")
    if not raw:
        return None
    return json.loads(raw).get("password")


def visit_count():
    """One round trip, so the page proves the database is actually reachable."""
    with psycopg.connect(
        host=HOST, dbname=NAME, user=USER, password=password(), connect_timeout=5
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS visits (at timestamptz DEFAULT now())")
            cur.execute("INSERT INTO visits DEFAULT VALUES")
            cur.execute("SELECT count(*) FROM visits")
            return cur.fetchone()[0]


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>YourCo</title>
<style>
  body {{ font-family: ui-monospace, Menlo, monospace; background:#f7f2e6;
         color:#22304a; display:grid; place-items:center; min-height:100vh;
         margin:0; }}
  main {{ text-align:center; }}
  h1 {{ font-size:1.4rem; margin:0 0 0.5rem; }}
  p {{ color:rgba(34,48,74,0.65); margin:0.25rem; font-size:0.9rem; }}
</style>
<main>
  <h1>{headline}</h1>
  <p>{detail}</p>
  <p>load balancer &rarr; fargate task &rarr; postgres</p>
</main>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="text/html; charset=utf-8"):
        payload = body.encode()
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        # The health check must not depend on the database: if Postgres
        # blips, you want a degraded page, not every task pulled out of the
        # load balancer at once.
        if self.path == "/healthz":
            return self._send(200, "ok\n", "text/plain")

        try:
            count = visit_count()
            body = PAGE.format(headline="It works.",
                               detail=f"{count} visits recorded in Postgres")
        except Exception as e:  # noqa: BLE001 — the page IS the error report
            body = PAGE.format(headline="Running, but no database.", detail=str(e)[:200])
        self._send(200, body)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)  # awslogs reads stdout


if __name__ == "__main__":
    print(f"listening on :{PORT}, database host {HOST or '(unset)'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
