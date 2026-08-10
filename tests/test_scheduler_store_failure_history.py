"""Tests for SQLSchedulerStore.consecutive_engine_failures().

Regression context, 2026-08-10: unified_bq.gold_dbt_pw_bq failed on the native engine 4
times in a row across 8 days, 4 different error messages -- and adaptive routing's scoring
had no memory of that history at all, so a repeatedly-failing destination scored identically
to one never tried. This is the query that gives routing that memory.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.scheduler_store import SQLSchedulerStore


def _seed_table(session, *, table_name: str = "events") -> uuid.UUID:
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    session.add(Source(id=source_id, name=f"src-{source_id}", type="rest", config={}))
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name="analytics",
            table_name=table_name,
            is_active=True,
        )
    )
    session.commit()
    return table_id


def _seed_destination(session, *, name: str, engine: str = "native") -> uuid.UUID:
    dest_id = uuid.uuid4()
    session.add(Destination(id=dest_id, name=name, engine=engine, config={}))
    session.commit()
    return dest_id


def _seed_job(session, *, table_id, destination_id, status: str, started_at: datetime) -> None:
    run_id = uuid.uuid4()
    session.add(MaintenanceRun(id=run_id, kind="adhoc", status="succeeded"))
    session.flush()
    session.add(
        MaintenanceJob(
            id=uuid.uuid4(),
            run_id=run_id,
            table_id=table_id,
            destination_id=destination_id,
            mode="standard",
            status=status,
            started_at=started_at,
            finished_at=started_at + timedelta(minutes=5),
        )
    )
    session.commit()


def test_consecutive_engine_failures_counts_most_recent_streak(session) -> None:
    table_id = _seed_table(session)
    native = _seed_destination(session, name="native-1", engine="native")
    base = datetime(2026, 8, 1, tzinfo=UTC)

    # Oldest -> newest: succeeded, then 3 failures in a row (the most recent 3).
    _seed_job(session, table_id=table_id, destination_id=native, status="succeeded", started_at=base)
    _seed_job(
        session,
        table_id=table_id,
        destination_id=native,
        status="failed",
        started_at=base + timedelta(days=2),
    )
    _seed_job(
        session,
        table_id=table_id,
        destination_id=native,
        status="failed",
        started_at=base + timedelta(days=4),
    )
    _seed_job(
        session,
        table_id=table_id,
        destination_id=native,
        status="failed",
        started_at=base + timedelta(days=6),
    )

    store = SQLSchedulerStore(session_factory=lambda: session, redis_client=None)
    result = store.consecutive_engine_failures([str(table_id)])

    assert result[str(table_id)][str(native)] == 3


def test_consecutive_engine_failures_stops_at_first_non_failed_status(session) -> None:
    table_id = _seed_table(session)
    native = _seed_destination(session, name="native-1", engine="native")
    base = datetime(2026, 8, 1, tzinfo=UTC)

    # Oldest -> newest: failed, failed, succeeded, failed. Only the most recent 1 counts --
    # the succeeded job breaks the streak even though there are older failures behind it.
    for i, status in enumerate(["failed", "failed", "succeeded", "failed"]):
        _seed_job(
            session,
            table_id=table_id,
            destination_id=native,
            status=status,
            started_at=base + timedelta(days=i * 2),
        )

    store = SQLSchedulerStore(session_factory=lambda: session, redis_client=None)
    result = store.consecutive_engine_failures([str(table_id)])

    assert result[str(table_id)][str(native)] == 1


def test_consecutive_engine_failures_tracks_multiple_destinations_independently(session) -> None:
    table_id = _seed_table(session)
    native = _seed_destination(session, name="native-1", engine="native")
    spark = _seed_destination(session, name="spark-1", engine="spark_emr")
    base = datetime(2026, 8, 1, tzinfo=UTC)

    for i in range(3):
        _seed_job(
            session,
            table_id=table_id,
            destination_id=native,
            status="failed",
            started_at=base + timedelta(days=i * 2),
        )
    _seed_job(
        session, table_id=table_id, destination_id=spark, status="succeeded", started_at=base
    )

    store = SQLSchedulerStore(session_factory=lambda: session, redis_client=None)
    result = store.consecutive_engine_failures([str(table_id)])

    assert result[str(table_id)][str(native)] == 3
    assert str(spark) not in result.get(str(table_id), {})


def test_consecutive_engine_failures_empty_input_returns_empty_dict(session) -> None:
    store = SQLSchedulerStore(session_factory=lambda: session, redis_client=None)
    assert store.consecutive_engine_failures([]) == {}


def test_consecutive_engine_failures_table_with_no_history_is_absent(session) -> None:
    table_id = _seed_table(session)
    store = SQLSchedulerStore(session_factory=lambda: session, redis_client=None)
    result = store.consecutive_engine_failures([str(table_id)])
    assert str(table_id) not in result
