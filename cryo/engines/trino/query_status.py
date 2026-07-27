"""Probe Trino for in-flight / completed maintenance queries."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import Any

from cryo.domain.enums import OperationType
from cryo.engines.trino.client import TrinoCoordinator, connect

logger = logging.getLogger(__name__)

_ACTIVE_STATES = frozenset(
    {
        "DISPATCHING",
        "STARTING",
        "RUNNING",
        "WAITING_FOR_RESOURCES",
        "QUEUED",
        "PLANNING",
        "FINISHING",
    }
)
_SUCCESS_STATES = frozenset({"FINISHED"})
_FAILED_STATES = frozenset({"CANCELED", "CANCELLED", "FAILED"})


class EngineOpState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    NOT_APPLICABLE = "not_applicable"


_OP_SQL_FRAGMENTS: dict[OperationType, tuple[str, ...]] = {
    OperationType.OPTIMIZE: ("execute optimize", "optimize("),
    OperationType.EXPIRE_SNAPSHOTS: ("expire_snapshots",),
    OperationType.REMOVE_ORPHAN_FILES: ("remove_orphan_files",),
    OperationType.OPTIMIZE_MANIFESTS: ("optimize_manifests", "rewrite_manifests"),
    OperationType.COMPUTE_STATISTICS: ("compute_statistics", "analyze "),
}


def get_query_state(
    coordinator: TrinoCoordinator,
    query_id: str,
    *,
    connector: Callable[[TrinoCoordinator], Any] = connect,
) -> EngineOpState:
    safe_id = "".join(ch for ch in query_id if ch.isalnum() or ch in frozenset({"-", "_"}))
    if not safe_id or safe_id != query_id:
        return EngineOpState.UNCERTAIN
    connection = connector(coordinator)
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"""
            SELECT state
            FROM system.runtime.queries
            WHERE query_id = '{safe_id}'
            """
        )
        row = cursor.fetchone()
        if row is None:
            return EngineOpState.UNCERTAIN
        return _map_state(str(row[0]))
    except Exception:
        logger.debug("Trino get_query_state failed for %s", query_id, exc_info=True)
        return EngineOpState.UNCERTAIN
    finally:
        cursor.close()
        connection.close()


def find_maintenance_query_state(
    coordinators: Sequence[TrinoCoordinator],
    table_ref: str | None,
    operation: OperationType | str | None,
    *,
    detail: str | None = None,
    connector: Callable[[TrinoCoordinator], Any] = connect,
) -> EngineOpState:
    """Probe all coordinators for an active/recent maintenance query.

    Returns RUNNING if any active match, SUCCEEDED if a finished match and no
    active match, FAILED if only failed matches, else UNCERTAIN.
    """
    if not coordinators:
        return EngineOpState.UNCERTAIN

    saw_success = False
    saw_failed = False
    for coordinator in coordinators:
        state = _probe_one(
            coordinator,
            table_ref=table_ref,
            operation=operation,
            detail=detail,
            connector=connector,
        )
        if state is EngineOpState.RUNNING:
            return EngineOpState.RUNNING
        if state is EngineOpState.SUCCEEDED:
            saw_success = True
        elif state is EngineOpState.FAILED:
            saw_failed = True

    if saw_success:
        return EngineOpState.SUCCEEDED
    if saw_failed:
        return EngineOpState.FAILED
    return EngineOpState.UNCERTAIN


def _probe_one(
    coordinator: TrinoCoordinator,
    table_ref: str | None,
    operation: OperationType | str | None,
    detail: str | None,
    connector: Callable[[TrinoCoordinator], Any],
) -> EngineOpState:
    connection = connector(coordinator)
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT query_id, state, query
            FROM system.runtime.queries
            WHERE query NOT LIKE '%system.runtime.queries%'
            ORDER BY created DESC
            LIMIT 200
            """
        )
        rows = cursor.fetchall() or ()
    except Exception:
        logger.debug(
            "Trino maintenance probe failed on %s",
            coordinator.label,
            exc_info=True,
        )
        return EngineOpState.UNCERTAIN
    finally:
        cursor.close()
        connection.close()

    op = OperationType(operation) if isinstance(operation, str) else operation
    fragments = _OP_SQL_FRAGMENTS.get(op, ()) if op is not None else ()
    table_needle = (table_ref or "").lower()
    detail_needle = (detail or "").strip().lower()

    best = EngineOpState.UNCERTAIN
    for _query_id, state_raw, query in rows:
        query_text = str(query or "")
        q_lower = query_text.lower()
        if table_needle and table_needle not in q_lower:
            continue
        detail_exact = query_text.strip() == (detail or "").strip()
        if detail_needle and detail_needle not in q_lower and not detail_exact:
            if fragments and any(frag in q_lower for frag in fragments):
                pass
            else:
                continue
        elif fragments and not any(frag in q_lower for frag in fragments):
            if detail_needle and detail_needle in q_lower:
                pass
            else:
                continue

        mapped = _map_state(str(state_raw))
        if mapped is EngineOpState.RUNNING:
            return EngineOpState.RUNNING
        if mapped is EngineOpState.SUCCEEDED:
            best = EngineOpState.SUCCEEDED
        elif mapped is EngineOpState.FAILED:
            if best is EngineOpState.UNCERTAIN:
                best = EngineOpState.FAILED
    return best


def _map_state(state: str) -> EngineOpState:
    upper = state.upper()
    if upper in _ACTIVE_STATES:
        return EngineOpState.RUNNING
    if upper in _SUCCESS_STATES:
        return EngineOpState.SUCCEEDED
    if upper in _FAILED_STATES:
        return EngineOpState.FAILED
    return EngineOpState.UNCERTAIN
