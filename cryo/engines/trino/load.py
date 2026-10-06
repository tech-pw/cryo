from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cryo.domain.models import CapacitySnapshot
from cryo.engines.trino.client import TrinoCoordinator, connect

OPTIMIZE_LOAD_SQL = """
SELECT count(*)
FROM system.runtime.queries
WHERE state IN ('RUNNING', 'QUEUED')
  AND query LIKE '%EXECUTE optimize%'
  AND query NOT LIKE '%system.runtime.queries%'
"""


def count_running_optimizes(
    coordinator: TrinoCoordinator,
    connector: Callable[[TrinoCoordinator], Any] = connect,
) -> int:
    connection: Any = connector(coordinator)
    cursor: Any = connection.cursor()
    try:
        cursor.execute(OPTIMIZE_LOAD_SQL)
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    finally:
        cursor.close()
        connection.close()


def probe_capacity(
    coordinators: tuple[TrinoCoordinator, ...],
    per_replica_cap: int,
    connector: Callable[[TrinoCoordinator], Any] = connect,
) -> CapacitySnapshot:
    used = sum(count_running_optimizes(coordinator, connector) for coordinator in coordinators)
    return CapacitySnapshot(capacity=max(0, per_replica_cap) * len(coordinators), used=used)
