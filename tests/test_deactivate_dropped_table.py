"""Critical vitals action plan, 2026-09-20, P1: Worker._deactivate_dropped_table must both
deactivate the table (existing behavior) AND record missing-metadata containment (new), and
must log rather than silently swallow either call's failure -- confirmed live, a table hit
is_table_not_found_error==True and stayed is_active=True with zero trace of why.
"""

from __future__ import annotations

from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import InMemoryJobStore, Worker


def _worker() -> Worker:
    return Worker(InMemoryMaintenanceQueue(), InMemoryJobStore(), lambda d: None, InMemoryLockStore())


def test_deactivate_dropped_table_calls_both_deactivate_and_record_missing_metadata() -> None:
    worker = _worker()
    store: InMemoryJobStore = worker._store  # type: ignore[assignment]

    worker._deactivate_dropped_table("table-1", "NoSuchKeyException: metadata.json")

    assert store.deactivated_tables["table-1"] == (
        "Table not found in catalog during maintenance: NoSuchKeyException: metadata.json"
    )
    assert store.missing_metadata_tables["table-1"] == (
        "Table not found in catalog during maintenance: NoSuchKeyException: metadata.json"
    )


def test_deactivate_dropped_table_logs_and_continues_when_deactivate_raises(caplog) -> None:
    worker = _worker()
    store: InMemoryJobStore = worker._store  # type: ignore[assignment]

    def _raise(table_id: str, reason: str) -> None:
        raise RuntimeError("db unavailable")

    store.deactivate_table = _raise  # type: ignore[method-assign]

    import logging

    with caplog.at_level(logging.ERROR, logger="cryo.worker.worker"):
        worker._deactivate_dropped_table("table-1", "NoSuchKeyException")

    assert "Failed to deactivate table" in caplog.text
    # The second mechanism must still run even though the first raised.
    assert store.missing_metadata_tables["table-1"] == (
        "Table not found in catalog during maintenance: NoSuchKeyException"
    )


def test_deactivate_dropped_table_logs_when_record_missing_metadata_raises(caplog) -> None:
    worker = _worker()
    store: InMemoryJobStore = worker._store  # type: ignore[assignment]

    def _raise(table_id: str, reason: str) -> None:
        raise RuntimeError("db unavailable")

    store.record_missing_metadata = _raise  # type: ignore[method-assign]

    import logging

    with caplog.at_level(logging.ERROR, logger="cryo.worker.worker"):
        worker._deactivate_dropped_table("table-1", "NoSuchKeyException")

    assert "Failed to record missing-metadata containment" in caplog.text
    # The first mechanism must have still succeeded.
    assert store.deactivated_tables["table-1"] == (
        "Table not found in catalog during maintenance: NoSuchKeyException"
    )


def test_deactivate_dropped_table_tolerates_a_store_without_record_missing_metadata() -> None:
    """A store implementation that hasn't added record_missing_metadata yet (e.g. an older
    test double) must not break the existing deactivate_table behavior -- the getattr/callable
    guard in _deactivate_dropped_table must skip the second mechanism entirely, not raise."""

    class _LegacyStore:
        def __init__(self) -> None:
            self.deactivated: dict[str, str] = {}

        def deactivate_table(self, table_id: str, reason: str) -> None:
            self.deactivated[table_id] = reason

    worker = _worker()
    worker._store = _LegacyStore()  # type: ignore[assignment]

    worker._deactivate_dropped_table("table-1", "NoSuchKeyException")

    assert worker._store.deactivated["table-1"] == (
        "Table not found in catalog during maintenance: NoSuchKeyException"
    )
