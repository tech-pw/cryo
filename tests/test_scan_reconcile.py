"""Tests for cryo/scanner/control.py::reconcile_stale_scan_chunks -- recovers scan chunks
orphaned by a worker that died mid-chunk (redeploy, OOM, node eviction, ...). Before this, a
chunk stuck in status='running' with no active worker had no recovery path: the run just
stalled forever with zero consumers and no visible error, unlike the maintenance job queue
which already has this via cryo/queue/reconcile.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from cryo.db.models import MaintenanceRun
from cryo.queue.scan_queue import InMemoryScanQueue
from cryo.scanner.control import reconcile_stale_scan_chunks
from tests.conftest import (
    _assign_missing_autoincrement_primary_keys,
    _assign_missing_uuid_primary_keys,
)


def _bound_session_factory(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    event.listen(factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    return factory


def _make_running_scan(
    session,
    *,
    heartbeat_at: datetime | None,
    chunk_status: str = "running",
) -> MaintenanceRun:
    chunk = {
        "status": chunk_status,
        "discovered": 100,
        "assigned": 25,
        "scanned": 10,
        "skipped": 0,
        "failed": 0,
    }
    if heartbeat_at is not None:
        chunk["heartbeat_at"] = heartbeat_at.isoformat()
    run = MaintenanceRun(
        id=uuid4(),
        kind="scan",
        status="running",
        summary={
            "scope": "source",
            "source_id": str(uuid4()),
            "table_ids": [],
            "table_names": [],
            "schema_names": [],
            "cost_full_scan": False,
            "full_scan": True,
            "chunks_total": 1,
            "chunks": {"0": chunk},
        },
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_reconciles_chunk_with_no_heartbeat_past_threshold(session) -> None:
    stale_heartbeat = datetime.now(UTC) - timedelta(seconds=1000)
    run = _make_running_scan(session, heartbeat_at=stale_heartbeat)
    queue = InMemoryScanQueue()

    reconciled = reconcile_stale_scan_chunks(
        _bound_session_factory(session), queue, stale_after_seconds=900
    )

    assert reconciled == 1
    assert queue.pending_count() == 1
    claimed = queue.claim()
    assert claimed is not None
    assert claimed["run_id"] == str(run.id)
    assert claimed["chunk_id"] == 0
    assert claimed["source_id"] == run.summary["source_id"]

    session.refresh(run)
    assert run.summary["chunks"]["0"]["status"] == "queued"


def test_does_not_touch_chunk_with_recent_heartbeat(session) -> None:
    fresh_heartbeat = datetime.now(UTC) - timedelta(seconds=5)
    run = _make_running_scan(session, heartbeat_at=fresh_heartbeat)
    queue = InMemoryScanQueue()

    reconciled = reconcile_stale_scan_chunks(
        _bound_session_factory(session), queue, stale_after_seconds=900
    )

    assert reconciled == 0
    assert queue.pending_count() == 0
    session.refresh(run)
    assert run.summary["chunks"]["0"]["status"] == "running"


def test_does_not_touch_chunk_already_finished(session) -> None:
    stale_heartbeat = datetime.now(UTC) - timedelta(seconds=1000)
    _make_running_scan(session, heartbeat_at=stale_heartbeat, chunk_status="succeeded")
    queue = InMemoryScanQueue()

    reconciled = reconcile_stale_scan_chunks(
        _bound_session_factory(session), queue, stale_after_seconds=900
    )

    assert reconciled == 0
    assert queue.pending_count() == 0


def test_chunk_with_no_heartbeat_at_all_is_treated_as_stale(session) -> None:
    """A chunk claimed under the pre-heartbeat code path (or one where record_scan_chunk_started
    never got to write heartbeat_at before the worker died) has no heartbeat field at all --
    that must still be reconcilable, not silently ignored forever."""
    _make_running_scan(session, heartbeat_at=None)
    queue = InMemoryScanQueue()

    reconciled = reconcile_stale_scan_chunks(
        _bound_session_factory(session), queue, stale_after_seconds=900
    )

    assert reconciled == 1
