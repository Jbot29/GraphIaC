"""The GraphIaC backend server — hand-rolled, no framework.

Serves the sandbox (src/GraphIaC/web/) and a JSON API that turns the
editor into a control panel over a *workspace* — the directory holding
the infra file passed to `serve`. Every .giac file under that directory
(subdirectories included) is loadable in-session; each keeps its own
state DB.

    GET  /               the sandbox (index.html, graphiac.js, registry.js)
    GET  /api/files      every .giac in the workspace               {files, default, state}
    GET  /api/source     a .giac source on disk                     ?file=rel/path.giac
    POST /api/source     save source back to disk (creates new)     {source, file?}
    POST /api/plan       parse + load + plan                        {source, file?}
    POST /api/run        parse + load + run (applies to AWS!)       {source, file?}
    POST /api/verify     parse + load + verify                      {source, file?}
    POST /api/agent      ask Claude to edit / explain the source    {source, request, file?, context?, history?}
    GET  /api/history    source snapshots, newest first              ?file=…            {versions}
    GET  /api/history    one snapshot with its text                  ?file=…&id=N       {version}
    POST /api/snapshot   record an accepted agent edit               {source, file?, event, message?}

`file` is always workspace-relative (e.g. "newsletter/newsletter.giac");
it defaults to the file `serve` was started with. State per script:
local mode keeps `<script>.db` next to each script; `--state s3://…`
maps each script to `<prefix>/<relative-path>.db` so same-named scripts
in different subdirectories cannot collide.

Design notes:
  - `Api` is transport-agnostic: plain dicts in, (status, dict) out. The
    local http.server handler below is one caller; the future
    Lambda-hosted deployment is another. Keep AWS/engine logic in Api,
    HTTP mechanics in Handler.
  - One engine operation at a time: a non-blocking lock returns 409
    ("busy") rather than queueing — the UI shows it and the user retries.
  - Binds 127.0.0.1 by default. There is no auth yet; do not bind wider
    until Cognito lands.
  - Source history lives in the same per-script DB as the state (table
    `source_versions`, db.py): every save, accepted agent edit, and run.
    With `--state s3://…` only run snapshots are recorded — they ride the
    run's publish; a save never touches S3, so there is nowhere to put one.
  - The agent (agent.py) is optional: it exists when the Anthropic SDK is
    installed and credentials are set; /api/files reports `agent` so the
    UI knows whether to show the drawer. It only ever returns proposed
    source — saving, planning and running stay with the person.

Start it:  python -m GraphIaC <profile> --infra_file site.giac serve
"""

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import GraphIaC
from GraphIaC import dsl
from GraphIaC.db import SOURCE_EVENTS, create_tables, get_version, list_versions, record_source
from GraphIaC.dsl import BlockedItem
from GraphIaC.main import summarize_ops
from GraphIaC.models import BaseEdge

from .logs import setup_logger

logger = setup_logger()

WEB_DIR = Path(__file__).parent / "web"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def _op_json(op):
    """One plan/run Operation as the JSON the UI renders and badges with."""
    obj = op.obj
    if isinstance(obj, BlockedItem):
        return {"op": op.operation.value, "type": obj.type, "label": obj.g_id, "reason": obj.reason}
    if isinstance(obj, BaseEdge):
        label = f"{obj.source_g_id} → {obj.destination_g_id}"
    else:
        label = obj.g_id
    return {"op": op.operation.value, "type": obj.__class__.__name__, "label": label}


class Api:
    def __init__(self, session, infra_path, state_url=None):
        self.session = session
        self.infra_path = Path(infra_path)
        self.root = self.infra_path.parent.resolve()  # the workspace
        self.default_file = self.infra_path.name
        self.state_url = state_url  # s3://bucket[/prefix] — None = local .db
        self.lock = threading.Lock()  # one engine operation at a time
        self.agent = None  # set lazily — see post_agent / agent_available

    def _resolve(self, file):
        """A workspace-relative .giac path -> (absolute Path, relative posix
        str), or (None, None) for anything that escapes the workspace.
        Resolves symlinks first, so a link pointing outside is rejected too."""
        rel = file if file is not None else self.default_file
        if not isinstance(rel, str) or not rel.endswith(".giac") or Path(rel).is_absolute():
            return None, None
        target = (self.root / rel).resolve()
        try:
            return target, target.relative_to(self.root).as_posix()
        except ValueError:  # escaped the workspace
            return None, None

    def _backend(self, db_name):
        """A fresh S3State per operation, so every request works against
        the latest published state. db_name is workspace-relative so
        subdirectory scripts get distinct keys under the prefix."""
        if not self.state_url:
            return None
        from GraphIaC.state import S3State

        return S3State(self.session, self.state_url, db_name)

    # ---- workspace ----
    def get_files(self):
        files = sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*.giac")
            if not any(part.startswith(".") for part in p.relative_to(self.root).parts)
        )
        return 200, {
            "files": files,
            "default": self.default_file,
            "state": self.state_url,
            "agent": self.agent_available(),
        }

    # ---- the agent ----
    def agent_available(self):
        if self.agent is not None:
            return True
        from GraphIaC import agent

        return agent.available()

    def post_agent(self, body):
        """Ask the agent. No engine lock: it never touches state or AWS. The
        answer is a proposal — the UI shows a diff and the person accepts it
        into the editor (or not)."""
        if "source" not in body or not body.get("request"):
            return 400, {"error": 'missing "source" or "request"'}
        if self.agent is None:
            if not self.agent_available():
                return 503, {"error": "agent not configured — pip install \"GraphIaC[agent]\" and set ANTHROPIC_API_KEY"}
            from GraphIaC.agent import Agent

            self.agent = Agent()
        _, rel = self._resolve(body.get("file"))
        files = self.get_files()[1]["files"]
        try:
            return 200, self.agent.ask(
                body["source"],
                body["request"],
                file=rel,
                files=files,
                context=body.get("context"),
                history=body.get("history") or (),
            )
        except Exception as e:  # noqa: BLE001 — surface model/API failures as JSON, keep serving
            logger.error(f"agent error: {e}")
            return 502, {"error": f"{e.__class__.__name__}: {e}"}

    # ---- source file ----
    def get_source(self, file=None):
        path, rel = self._resolve(file)
        if not path:
            return 400, {"error": f"not a .giac file in the workspace: {file}"}
        source = path.read_text() if path.exists() else ""
        return 200, {"source": source, "file": rel, "path": str(path), "state": self.state_url}

    def post_source(self, body):
        if "source" not in body:
            return 400, {"error": 'missing "source"'}
        path, rel = self._resolve(body.get("file"))
        if not path:
            return 400, {"error": f'not a .giac file in the workspace: {body.get("file")}'}
        path.parent.mkdir(parents=True, exist_ok=True)  # new scripts may sit in new subdirs
        path.write_text(body["source"])
        version = self._snapshot(path, body["source"], "save")
        return 200, {"saved": True, "file": rel, "path": str(path), "version": version}

    # ---- source history ----
    def _snapshot(self, path, source, event, message=None):
        """Record a save/agent snapshot in the script's local DB. None in S3
        mode: the DB there only moves on run (see the module docstring)."""
        if self.state_url:
            return None
        conn = sqlite3.connect(str(path.with_suffix(".db")))
        try:
            create_tables(conn)
            return record_source(conn, source, event, message)
        finally:
            conn.close()

    def post_snapshot(self, body):
        if "source" not in body or body.get("event") not in SOURCE_EVENTS or body.get("event") == "run":
            return 400, {"error": 'missing "source", or event not one of save|agent'}
        path, rel = self._resolve(body.get("file"))
        if not path:
            return 400, {"error": f'not a .giac file in the workspace: {body.get("file")}'}
        version = self._snapshot(path, body["source"], body["event"], body.get("message"))
        if version is None and self.state_url:
            return 200, {"version": None, "note": "save/agent snapshots are not recorded with --state s3://… (run snapshots are)"}
        return 200, {"version": version, "file": rel}

    def get_history(self, file=None, version_id=None):
        """The snapshots for a script — read lock-free, like plan."""
        path, rel = self._resolve(file)
        if not path:
            return 400, {"error": f"not a .giac file in the workspace: {file}"}
        backend = self._backend(Path(rel).with_suffix(".db").as_posix())
        try:
            db_path = backend.fetch() if backend else str(path.with_suffix(".db"))
            conn = sqlite3.connect(db_path)
            try:
                create_tables(conn)
                if version_id is not None:
                    v = get_version(conn, version_id)
                    return (200, {"version": v, "file": rel}) if v else (404, {"error": f"no version {version_id}"})
                return 200, {"versions": list_versions(conn), "file": rel, "state": self.state_url}
            finally:
                conn.close()
        finally:
            if backend:
                backend.cleanup()

    # ---- engine ----
    def _load(self, source, path, backend=None):
        """source -> (state, blocked, error_response). Fresh state per call;
        the DB comes from S3 (via backend) or the .db next to the script —
        exactly what the CLI does."""
        res = dsl.parse(source)
        if res["errors"]:
            return None, None, (400, {"errors": res["errors"], "warnings": res["warnings"]})
        conn = sqlite3.connect(backend.fetch() if backend else str(path.with_suffix(".db")))
        state = GraphIaC.init(self.session, conn)
        try:
            blocked = dsl.load_graph(state, res["graph"], base_dir=path.parent)
        except FileNotFoundError as e:
            conn.close()
            return None, None, (400, {"error": str(e)})
        return state, blocked, None

    def _engine(self, body, fn, s3_lock_op=None):
        """s3_lock_op: set (e.g. "run") for operations that mutate state —
        takes the S3 lock and publishes the DB back; reads stay lock-free."""
        if "source" not in body:
            return 400, {"error": 'missing "source"'}
        path, rel = self._resolve(body.get("file"))
        if not path:
            return 400, {"error": f'not a .giac file in the workspace: {body.get("file")}'}
        if not self.lock.acquire(blocking=False):
            return 409, {"error": "another operation is running — try again"}
        backend = None
        try:
            backend = self._backend(Path(rel).with_suffix(".db").as_posix())
            if backend and s3_lock_op:
                from GraphIaC.state import LockHeld

                try:
                    backend.acquire(s3_lock_op)
                except LockHeld as e:
                    return 423, {"error": str(e)}  # HTTP 423 Locked
            state, blocked, err = self._load(body["source"], path, backend)
            if err:
                return err
            try:
                return fn(state, blocked)
            finally:
                state.db_conn.close()
                if backend and s3_lock_op:
                    # publish even after a partial run (recording what WAS
                    # created beats orphaning it), then always unlock
                    try:
                        backend.publish()
                    finally:
                        backend.release()
        except Exception as e:  # surface engine/AWS failures as JSON, keep serving
            logger.error(f"engine error: {e}")
            return 500, {"error": f"{e.__class__.__name__}: {e}"}
        finally:
            if backend:
                backend.cleanup()
            self.lock.release()

    def _guards(self, source):
        """Evaluate the source's ? guards; [] when none or unparseable."""
        from GraphIaC import guards

        res = dsl.parse(source)
        if res["errors"] or not res["graph"]["guards"]:
            return []
        return [g.model_dump() for g in guards.evaluate(self.session, res["graph"])]

    def post_plan(self, body):
        return self._engine(
            body, lambda state, blocked: (200, {"ops": [_op_json(o) for o in GraphIaC.plan(state, blocked)]})
        )

    def post_run(self, body):
        def go(state, blocked):
            ops = None
            try:
                ops = GraphIaC.run(state, blocked)
            finally:
                # the file as applied — recorded even after a partial run,
                # on the same connection, so in S3 mode it publishes with the state
                version = record_source(state.db_conn, body["source"], "run", summarize_ops(ops))
            applied = [_op_json(o) for o in ops]
            return 200, {"applied": applied, "guards": self._guards(body["source"]), "version": version}

        return self._engine(body, go, s3_lock_op="run")

    def post_verify(self, body):
        def go(state, blocked):
            checks = []
            failed = GraphIaC.verify(state, collected=checks)
            results = self._guards(body["source"])
            failed += sum(1 for r in results if r["status"] == "fail")
            return 200, {"checks": checks, "failed": failed, "guards": results}

        return self._engine(body, go)


class Handler(BaseHTTPRequestHandler):
    api: Api = None  # set by serve()

    def _json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / name).resolve()
        if target.parent != WEB_DIR.resolve() or not target.is_file():
            return self._json(404, {"error": "not found"})
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/files":
            return self._json(*self.api.get_files())
        if url.path == "/api/source":
            file = parse_qs(url.query).get("file", [None])[0]
            return self._json(*self.api.get_source(file))
        if url.path == "/api/history":
            q = parse_qs(url.query)
            vid = q.get("id", [None])[0]
            if vid is not None and not vid.isdigit():
                return self._json(400, {"error": "id must be an integer"})
            return self._json(*self.api.get_history(q.get("file", [None])[0], int(vid) if vid else None))
        if url.path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        return self._static(url.path)

    def do_POST(self):
        path = self.path.split("?")[0]
        routes = {
            "/api/source": self.api.post_source,
            "/api/plan": self.api.post_plan,
            "/api/run": self.api.post_run,
            "/api/verify": self.api.post_verify,
            "/api/agent": self.api.post_agent,
            "/api/snapshot": self.api.post_snapshot,
        }
        if path not in routes:
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "body must be JSON"})
        return self._json(*routes[path](body))

    def log_message(self, format, *args):  # quiet http.server's per-request stderr lines
        logger.debug(f"{self.address_string()} {format % args}")


def serve(session, infra_path, port=8642, host="127.0.0.1", state_url=None):
    Handler.api = Api(session, infra_path, state_url=state_url)
    httpd = ThreadingHTTPServer((host, port), Handler)
    where = f" (state: {state_url})" if state_url else ""
    logger.plan(
        f"GraphIaC serving workspace {Handler.api.root} at http://{host}:{port}"
        f" (default: {Handler.api.default_file}){where}"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("bye")
    finally:
        httpd.server_close()
