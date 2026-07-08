import sqlite3
from enum import Enum
from typing import Any

import networkx as nx
from pydantic import BaseModel, Field, validator

from GraphIaC.model_map import BASE_MODEL_MAP

from .db import (
    create_tables,
    db_create_edge,
    db_create_node,
    db_delete_row,
    db_get_rows_not_in_list,
    db_update_node,
    get_edge_by_id,
    get_node_by_id,
)
from .logs import setup_logger

logger = setup_logger()


class GraphIaCState(BaseModel):
    session: Any = Field(default=None, exclude=True)
    db_conn: Any = Field(default=None, exclude=True)
    G: Any = Field(default=None, exclude=True)
    models_map: dict

    class Config:
        arbitrary_types_allowed = True

    @validator("db_conn")
    def validate_db_conn(cls, v):
        if not isinstance(v, sqlite3.Connection):
            raise ValueError("db_conn must be a valid sqlite3.Connection")
        return v


def init(session, db_conn):
    create_tables(db_conn)
    return GraphIaCState(
        session=session, db_conn=db_conn, G=nx.DiGraph(), models_map=BASE_MODEL_MAP
    )


def add_node(state, node):
    state.G.add_node(node.g_id, data=node)


def add_edge(state, edge):
    state.G.add_edge(edge.source_g_id, edge.destination_g_id, data=edge)


class OperationType(Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    IMPORT = "import"
    CREATE_EDGE = "create_edge"
    BLOCKED = "blocked"


class Operation(BaseModel):
    operation: OperationType
    obj: Any = Field(default=None, exclude=True)


def load_model_from_db(state, obj_name, obj_data):
    pm = state.models_map[obj_name]
    return pm.model_validate_json(obj_data)


def _diff_summary(old, new):
    """Return a compact human-readable summary of changed fields."""
    old_d = old.model_dump()
    new_d = new.model_dump()
    parts = []
    for key in new_d:
        if key == "g_id":
            continue
        if old_d.get(key) != new_d[key]:
            parts.append(f"{key}: {old_d.get(key)!r} → {new_d[key]!r}")
    return "  " + ", ".join(parts) if parts else ""


def plan(state, blocked=None):
    """Diff code/DB/AWS into operations.

    `blocked` is the list of BlockedItem from dsl.load_graph() (empty for
    Python infra files): nodes/edges waiting on unresolved attribute
    references. They are reported as BLOCKED operations, and their DB rows
    are shielded from orphan deletion — a resource that was provisioned
    and later became blocked must not read as "removed from the code".
    """
    plan_ops = []
    db_nodes_seen = []

    for node in state.G.nodes:
        pn = state.G.nodes[node]["data"]
        cls_name = pn.__class__.__name__

        current_state = pn.read(state.session, state.G, g_id=pn.g_id, read_id=pn.read_id)

        if not current_state:
            logger.plan(f"  + {cls_name} [{pn.g_id}]  will be created")
            plan_ops.append(Operation(operation=OperationType.CREATE, obj=pn))
            continue

        pn_db_row = get_node_by_id(state.db_conn, pn.g_id)

        if not pn_db_row:
            logger.plan(f"  ↳ {cls_name} [{pn.g_id}]  will be imported")
            plan_ops.append(Operation(operation=OperationType.IMPORT, obj=current_state))
            continue

        db_nodes_seen.append(str(pn_db_row[0]))
        pn_last = load_model_from_db(state, pn_db_row[2], pn_db_row[3])

        if pn.diff(state.session, state.G, current_state) or pn.diff(
            state.session, state.G, pn_last
        ):
            summary = _diff_summary(pn_last, current_state)
            logger.plan(f"  ~ {cls_name} [{pn.g_id}]  will be updated{summary}")
            plan_ops.append(Operation(operation=OperationType.UPDATE, obj=current_state))

        state.G.nodes[node]["data"] = current_state

    for edge in list(state.G.edges(data=True)):
        edge_data = edge[2]["data"]
        cls_name = edge_data.__class__.__name__
        label = f"{edge_data.source_g_id} → {edge_data.destination_g_id}"
        edge_id = None

        live_edge = edge_data.read(state.session, state.G)

        source_id = get_node_by_id(state.db_conn, edge[0])
        destination_id = get_node_by_id(state.db_conn, edge[1])

        if source_id and destination_id:
            edge_id = get_edge_by_id(state.db_conn, source_id[0], destination_id[0])

        if not edge_id or not live_edge:
            logger.plan(f"  + {cls_name} [{label}]  will be applied")
            plan_ops.append(Operation(operation=OperationType.CREATE_EDGE, obj=edge_data))

    for b in blocked or []:
        logger.plan(f"  ⊘ {b.type} [{b.g_id}]  BLOCKED — {b.reason}")
        plan_ops.append(Operation(operation=OperationType.BLOCKED, obj=b))
        row = get_node_by_id(state.db_conn, b.g_id)
        if row:
            db_nodes_seen.append(str(row[0]))

    for orphaned_node in db_get_rows_not_in_list(state.db_conn, "nodes", db_nodes_seen):
        on_last = load_model_from_db(state, orphaned_node[2], orphaned_node[3])
        cls_name = on_last.__class__.__name__
        logger.plan(f"  - {cls_name} [{on_last.g_id}]  will be deleted")
        plan_ops.append(Operation(operation=OperationType.DELETE, obj=on_last))

    return plan_ops


def run(state, blocked=None):
    logger.plan("Planning...")
    changes = plan(state, blocked)

    if not changes:
        logger.info("No changes. Infrastructure is up to date.")
        return []

    counts = {OperationType.CREATE: 0, OperationType.UPDATE: 0,
              OperationType.DELETE: 0, OperationType.IMPORT: 0,
              OperationType.CREATE_EDGE: 0, OperationType.BLOCKED: 0}

    logger.plan("Applying...")
    for change in changes:
        obj = change.obj
        cls_name = obj.__class__.__name__
        counts[change.operation] += 1

        if change.operation == OperationType.BLOCKED:
            continue  # nothing to do — re-run once the upstream is ready

        if change.operation == OperationType.CREATE:
            logger.info(f"  + [{obj.g_id}] creating {cls_name}...")
            obj.create(state.session, state.G)
            db_create_node(state.db_conn, obj)

        elif change.operation == OperationType.IMPORT:
            logger.info(f"  ↳ [{obj.g_id}] importing {cls_name}")
            db_create_node(state.db_conn, obj)

        elif change.operation == OperationType.UPDATE:
            logger.info(f"  ~ [{obj.g_id}] updating {cls_name}")
            obj.update(state.session, state.G)
            db_update_node(state.db_conn, obj)

        elif change.operation == OperationType.DELETE:
            logger.info(f"  - [{obj.g_id}] deleting {cls_name}...")
            obj.delete(state.session, state.G)
            row_id = get_node_by_id(state.db_conn, obj.g_id)
            db_delete_row(state.db_conn, "nodes", row_id[0])

        elif change.operation == OperationType.CREATE_EDGE:
            label = f"{obj.source_g_id} → {obj.destination_g_id}"
            logger.info(f"  + [{label}] applying {cls_name}...")
            obj.create(state.session, state.G)
            db_create_edge(state.db_conn, obj.source_g_id, obj.destination_g_id, obj)

    created = counts[OperationType.CREATE]
    updated = counts[OperationType.UPDATE]
    deleted = counts[OperationType.DELETE]
    imported = counts[OperationType.IMPORT]
    edges = counts[OperationType.CREATE_EDGE]
    n_blocked = counts[OperationType.BLOCKED]
    parts = []
    if created:
        parts.append(f"{created} created")
    if updated:
        parts.append(f"{updated} updated")
    if deleted:
        parts.append(f"{deleted} deleted")
    if imported:
        parts.append(f"{imported} imported")
    if edges:
        parts.append(f"{edges} edges applied")
    if n_blocked:
        parts.append(f"{n_blocked} blocked (re-run when upstream is ready)")
    logger.plan(f"Done. {', '.join(parts)}.")
    return changes


def verify(state, collected=None):
    """Audit live AWS state; returns the failure count (for CI exit codes).

    Pass a list as `collected` to also receive every check as a dict
    (label/name/passed/message) — the HTTP API uses this.
    """
    logger.plan("Verifying infrastructure...")
    total_passed = 0
    total_failed = 0

    def _print_results(label, results):
        nonlocal total_passed, total_failed
        if not results:
            return
        logger.info(f"  {label}")
        for r in results:
            if collected is not None:
                collected.append(
                    {"label": label, "name": r.name, "passed": r.passed, "message": r.message}
                )
            if r.passed:
                logger.info(f"    ✓ {r.name}" + (f": {r.message}" if r.message else ""))
                total_passed += 1
            else:
                logger.warning(f"    ✗ {r.name}" + (f": {r.message}" if r.message else ""))
                total_failed += 1

    for node_id in state.G.nodes:
        node = state.G.nodes[node_id]["data"]
        live = node.read(state.session, state.G, g_id=node.g_id, read_id=node.read_id)
        if live:
            state.G.nodes[node_id]["data"] = live  # update graph so edges see current state
            results = live.verify(state.session, state.G)
        else:
            results = node.verify(state.session, state.G)
        _print_results(f"{node.__class__.__name__} [{node_id}]", results)

    for src, dst, data in state.G.edges(data=True):
        edge = data["data"]
        results = edge.verify(state.session, state.G)
        _print_results(f"{edge.__class__.__name__} [{src} → {dst}]", results)

    if total_passed == 0 and total_failed == 0:
        logger.plan("No verify() checks defined for this infrastructure.")
        return total_failed

    if total_failed:
        logger.warning(f"Summary: {total_passed} passed, {total_failed} failed.")
    else:
        logger.plan(f"All checks passed. {total_passed} passed, 0 failed.")

    return total_failed


def export_graph(state, file_name):
    A = nx.nx_agraph.to_agraph(state.G)
    A.write(f"{file_name}.dot")
    A.draw(f"{file_name}.png", prog="neato")
