import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .logs import setup_logger

logger = setup_logger()


class TableNode(BaseModel):
    id: int | None = Field(default=None)
    g_id: str
    type: str
    data: Any
    __tablename__ = "nodes"

    @classmethod
    def create_table_sql(cls) -> str:
        return """
        CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            g_id TEXT UNIQUE,
            type TEXT,
            data JSON
        );
        """


class TableEdge(BaseModel):
    id: int | None = Field(default=None)
    g_id: str | None = None
    source: int
    destination: int
    weight: float | None = None
    type: str | None = None
    data: Any = None

    @classmethod
    def create_table_sql(cls) -> str:
        return """
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            g_id TEXT UNIQUE,
            source INTEGER NOT NULL,
            destination INTEGER NOT NULL,
            weight REAL,
            type TEXT,
            data JSON,
            FOREIGN KEY (source) REFERENCES nodes(id) ON DELETE CASCADE,
            FOREIGN KEY (destination) REFERENCES nodes(id) ON DELETE CASCADE,
            UNIQUE(source, destination)
        );
        """


SOURCE_VERSIONS_SQL = """
CREATE TABLE IF NOT EXISTS source_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    sha TEXT NOT NULL,
    event TEXT NOT NULL,
    message TEXT,
    source TEXT NOT NULL
);
"""


def create_tables(conn):
    cursor = conn.cursor()
    cursor.execute(TableNode.create_table_sql())
    cursor.execute(TableEdge.create_table_sql())
    cursor.execute(SOURCE_VERSIONS_SQL)  # IF NOT EXISTS: older DBs grow the table on first open
    conn.commit()


# ---------------------------------------------------------------------
# Source history. The DB already records what was deployed; this records
# what was written, next to it. Linear, whole-text snapshots (a .giac file
# is a few KB), three events:
#     save   the editor wrote the file (skipped when the text is unchanged)
#     agent  the person accepted an agent edit (message = their request)
#     run    the file as it was when a run applied (always recorded — that
#            is the one git cannot give you)
# Rollback is: restore the text, plan, run. The planner does the rest.
# ---------------------------------------------------------------------
SOURCE_EVENTS = ("save", "agent", "run")


def _sha(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def record_source(conn, source, event, message=None):
    """Snapshot `source`. Returns the new row (without the text), or None
    when a save/agent snapshot would duplicate the latest one. Runs are
    always recorded: "as applied" is worth a row even if the text matches."""
    if event not in SOURCE_EVENTS:
        raise ValueError(f"unknown source event {event!r}")
    sha = _sha(source)
    cur = conn.cursor()
    latest = cur.execute("SELECT sha FROM source_versions ORDER BY id DESC LIMIT 1").fetchone()
    if event != "run" and latest and latest[0] == sha:
        return None
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur.execute(
        "INSERT INTO source_versions (ts, sha, event, message, source) VALUES (?, ?, ?, ?, ?)",
        (ts, sha, event, message, source),
    )
    conn.commit()
    return {"id": cur.lastrowid, "ts": ts, "sha": sha, "event": event, "message": message}


def list_versions(conn, limit=200):
    """Newest first, without the text."""
    rows = conn.execute(
        "SELECT id, ts, sha, event, message, length(source) FROM source_versions ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {"id": r[0], "ts": r[1], "sha": r[2], "event": r[3], "message": r[4], "bytes": r[5]} for r in rows
    ]


def get_version(conn, version_id):
    r = conn.execute(
        "SELECT id, ts, sha, event, message, source FROM source_versions WHERE id = ?", (version_id,)
    ).fetchone()
    if not r:
        return None
    return {"id": r[0], "ts": r[1], "sha": r[2], "event": r[3], "message": r[4], "source": r[5]}


def db_create_node(conn, node):
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO nodes (g_id, type, data) VALUES (?, ?, ?)",
            (node.g_id, node.__class__.__name__, node.model_dump_json()),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        logger.debug(f"db_create_node [{node.g_id}]: {e}")


def db_update_node(conn, node):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE nodes SET type = ?, data = ? WHERE g_id = ?",
        (node.__class__.__name__, node.model_dump_json(), node.g_id),
    )
    conn.commit()


def get_node_by_id(conn, name):
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM nodes WHERE g_id = ?", (name,))
        return cursor.fetchone()
    except sqlite3.IntegrityError as e:
        logger.debug(f"get_node_by_id [{name}]: {e}")


def get_edge_by_id(conn, s_name, d_name):
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM edges WHERE source = ? and destination = ?", (s_name, d_name)
        )
        return cursor.fetchone()
    except sqlite3.IntegrityError as e:
        logger.debug(f"get_edge_by_id [{s_name}->{d_name}]: {e}")


def db_create_edge(conn, source_name, destination_name, edge, weight=1):
    source = get_node_by_id(conn, source_name)[0]
    destination = get_node_by_id(conn, destination_name)[0]
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO edges (source, destination, weight, type, data) VALUES (?, ?, ?, ?, ?)",
            (source, destination, weight, edge.__class__.__name__, edge.model_dump_json()),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        logger.debug(f"db_create_edge [{source_name}->{destination_name}]: {e}")


def db_get_rows_not_in_list(conn, table_name, id_list):
    cursor = conn.cursor()
    if not id_list:
        query = f"SELECT * FROM {table_name}"
    else:
        query = f"SELECT * FROM {table_name} WHERE id NOT IN ({','.join(id_list)})"
    cursor.execute(query)
    return cursor.fetchall()


def db_delete_row(db_conn, table_name, row_id):
    try:
        cursor = db_conn.cursor()
        cursor.execute(f"DELETE FROM {table_name} WHERE id = ?", (row_id,))
        db_conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Error deleting row {row_id} from {table_name}: {e}")
        raise
