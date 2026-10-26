from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cryo.db.models import AuditLog, Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.job_dedupe import active_coverage, supersede_covered_jobs
from cryo.domain.enums import Mode, OperationType
from cryo.planner.guardrails import MODE_OPERATIONS
from cryo.planner.job_coverage import (
    ActiveJob,
    canonical_operations,
    dedupe_key,
    effective_operations,
    uncovered,
)
from cryo.planner.planner import build_plan
from cryo.queue.locks import InMemoryLockStore

_RICH_STATS = {
    "table_ref": "cat.db.events",
    "partition_col": "event_date",
    "total_files": 5000,
    "small_files_count": 4000,
    "total_size_gb": 500.0,
    "position_delete_files": 400,
    "equality_delete_files": 40,
    "delete_file_pct": 30.0,
    "snapshot_count": 900,
}
_RICH_PARAMS = {
    "expire_data_enabled": True,
    "expire_data_field": "event_date",
    "expire_data_retention": "7d",
    "expire_data_field_type": "date",
    "compute_statistics": True,
    "stats_compute_ndv": True,
    "min_small_files": 1,
    "convert_equality_deletes_enabled": True,
}
_PARTITIONS = (
    {"partition_value": "20000", "small_files": 900, "file_count": 1000, "size_gb": 50.0},
    {"partition_value": "20001", "small_files": 800, "file_count": 900, "size_gb": 40.0},
)


@pytest.mark.parametrize("mode", list(Mode))
def test_build_plan_never_emits_operations_outside_mode_operations(mode: Mode) -> None:
    plan = build_plan(
        _RICH_STATS, _PARTITIONS, mode, dict(_RICH_PARAMS), protected_snapshot_ids=[1]
    )

    emitted = {op.type for op in plan.operations}

    assert emitted <= MODE_OPERATIONS[mode]


def test_every_operation_is_reachable_from_some_mode() -> None:
    reachable = set().union(*MODE_OPERATIONS.values())

    assert {OperationType.OPTIMIZE, OperationType.REMOVE_ORPHAN_FILES} <= reachable


def test_deep_is_a_superset_of_standard_and_light() -> None:
    assert MODE_OPERATIONS[Mode.LIGHT] < MODE_OPERATIONS[Mode.STANDARD] < MODE_OPERATIONS[Mode.DEEP]


def test_dedupe_key_is_canonical_and_order_independent() -> None:
    assert dedupe_key(["expire_snapshots", "optimize"]) == dedupe_key(
        ["optimize", "expire_snapshots"]
    )
    assert dedupe_key(["expire_snapshots", "optimize"]) == "optimize,expire_snapshots"
    assert dedupe_key([]) is None
    assert dedupe_key(["not_an_operation"]) is None


def test_canonical_operations_drops_unknown_and_duplicates() -> None:
    assert canonical_operations(["optimize", "bogus", "optimize"]) == ("optimize",)


def test_effective_operations_prefers_job_then_run_then_mode() -> None:
    assert effective_operations(
        "deep", job_operations=["optimize"], run_operations=["expire_snapshots"]
    ) == {"optimize"}
    assert effective_operations("deep", run_operations=["expire_snapshots"]) == {"expire_snapshots"}
    assert effective_operations("light") == {"expire_snapshots", "expire_data"}
    assert effective_operations("nonsense") == frozenset()


def test_uncovered_fully_covered_when_active_job_has_every_requested_operation() -> None:
    active = [ActiveJob("j1", frozenset({"optimize", "expire_snapshots", "expire_data"}))]

    coverage = uncovered(["optimize", "expire_snapshots"], active)

    assert coverage.fully_covered
    assert coverage.skipped == ("optimize", "expire_snapshots")
    assert coverage.covering_job_ids == ("j1",)


def test_uncovered_partial_trims_to_remaining_in_canonical_order() -> None:
    active = [ActiveJob("j1", frozenset({"optimize"}))]

    coverage = uncovered(["expire_snapshots", "optimize", "remove_orphan_files"], active)

    assert coverage.partially_covered
    assert coverage.remaining == ("expire_snapshots", "remove_orphan_files")
    assert coverage.skipped == ("optimize",)


def test_uncovered_with_no_active_jobs_keeps_everything() -> None:
    coverage = uncovered(["optimize"], [])

    assert coverage.remaining == ("optimize",)
    assert coverage.skipped == ()
    assert not coverage.partially_covered


def test_uncovered_unions_coverage_across_active_jobs() -> None:
    active = [
        ActiveJob("a", frozenset({"optimize"})),
        ActiveJob("b", frozenset({"expire_snapshots"})),
    ]

    coverage = uncovered(["optimize", "expire_snapshots"], active)

    assert coverage.fully_covered
    assert set(coverage.covering_job_ids) == {"a", "b"}


def _seed_table(session) -> tuple[Table, Destination]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={})
    table = Table(
        id=uuid4(), source_id=source.id, schema_name="s", table_name=f"t{uuid4().hex[:6]}"
    )
    session.add_all([source, destination, table])
    session.flush()
    return table, destination


def _seed_job(
    session,
    table: Table,
    destination: Destination,
    *,
    status: str,
    mode: str = "standard",
    operations: list[str] | None = None,
    run_operations: list[str] | None = None,
    created_at: datetime | None = None,
    started_at: datetime | None = None,
    dedupe_key_value: str | None = "unset",
) -> MaintenanceJob:
    summary = {"operations": run_operations} if run_operations else {}
    run = MaintenanceRun(
        id=uuid4(),
        kind="adhoc",
        trigger="api",
        status="running",
        summary=summary,
        created_at=created_at or datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
    )
    session.add(run)
    session.flush()
    effective = effective_operations(mode, job_operations=operations, run_operations=run_operations)
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode=mode,
        operations=operations,
        dedupe_key=dedupe_key(effective) if dedupe_key_value == "unset" else dedupe_key_value,
        status=status,
        started_at=started_at,
    )
    session.add(job)
    session.commit()
    return job


def test_active_coverage_reads_job_run_then_mode_operations(session) -> None:
    table, destination = _seed_table(session)
    trimmed = _seed_job(session, table, destination, status="queued", operations=["optimize"])
    requested = _seed_job(
        session,
        table,
        destination,
        status="running",
        run_operations=["expire_snapshots"],
        dedupe_key_value=None,
    )
    by_mode = _seed_job(
        session, table, destination, status="deferred", mode="light", dedupe_key_value=None
    )
    _seed_job(session, table, destination, status="succeeded", dedupe_key_value=None)

    coverage = active_coverage(session, [table.id])

    jobs = {job.job_id: job.operations for job in coverage[str(table.id)]}
    assert jobs == {
        str(trimmed.id): frozenset({"optimize"}),
        str(requested.id): frozenset({"expire_snapshots"}),
        str(by_mode.id): frozenset({"expire_snapshots", "expire_data"}),
    }


def test_active_coverage_without_filter_covers_all_tables(session) -> None:
    first, destination = _seed_table(session)
    second, _ = _seed_table(session)
    _seed_job(session, first, destination, status="queued")
    _seed_job(session, second, destination, status="admitted")

    assert set(active_coverage(session)) == {str(first.id), str(second.id)}


def test_supersede_skips_queued_duplicates_whose_work_the_success_covered(session) -> None:
    table, destination = _seed_table(session)
    started = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    done = _seed_job(
        session, table, destination, status="succeeded", started_at=started, mode="standard"
    )
    covered = _seed_job(
        session,
        table,
        destination,
        status="queued",
        mode="light",
        created_at=started - timedelta(minutes=5),
    )

    superseded = supersede_covered_jobs(session, done.id)

    assert superseded == [(str(covered.id), str(destination.id))]
    session.refresh(covered)
    assert covered.status == "skipped"
    assert covered.error == f"superseded_by:{done.id}"
    assert covered.finished_at is not None
    audit = session.query(AuditLog).filter_by(job_id=covered.id, action="job_superseded").one()
    assert audit.status == "SKIPPED"


def test_supersede_leaves_jobs_that_asked_for_more_than_was_done(session) -> None:
    table, destination = _seed_table(session)
    started = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    done = _seed_job(session, table, destination, status="succeeded", started_at=started)
    deeper = _seed_job(
        session,
        table,
        destination,
        status="queued",
        mode="deep",
        created_at=started - timedelta(minutes=5),
    )

    assert supersede_covered_jobs(session, done.id) == []
    session.refresh(deeper)
    assert deeper.status == "queued"


def test_supersede_leaves_jobs_created_after_the_success_started(session) -> None:
    table, destination = _seed_table(session)
    started = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    done = _seed_job(session, table, destination, status="succeeded", started_at=started)
    fresh = _seed_job(
        session,
        table,
        destination,
        status="queued",
        mode="light",
        created_at=started + timedelta(minutes=5),
    )

    assert supersede_covered_jobs(session, done.id) == []
    session.refresh(fresh)
    assert fresh.status == "queued"


def test_supersede_never_touches_forced_jobs_or_other_tables(session) -> None:
    table, destination = _seed_table(session)
    other, _ = _seed_table(session)
    started = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    done = _seed_job(session, table, destination, status="succeeded", started_at=started)
    forced = _seed_job(
        session,
        table,
        destination,
        status="queued",
        mode="light",
        created_at=started - timedelta(minutes=5),
        dedupe_key_value=None,
    )
    elsewhere = _seed_job(
        session,
        other,
        destination,
        status="queued",
        mode="light",
        created_at=started - timedelta(minutes=5),
    )

    assert supersede_covered_jobs(session, done.id) == []
    session.refresh(forced)
    session.refresh(elsewhere)
    assert forced.status == "queued"
    assert elsewhere.status == "queued"


def test_supersede_ignores_running_and_admitted_jobs(session) -> None:
    table, destination = _seed_table(session)
    started = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    done = _seed_job(session, table, destination, status="succeeded", started_at=started)
    running = _seed_job(
        session,
        table,
        destination,
        status="running",
        mode="light",
        created_at=started - timedelta(minutes=5),
    )

    assert supersede_covered_jobs(session, done.id) == []
    session.refresh(running)
    assert running.status == "running"


def test_unique_index_blocks_a_second_active_job_with_the_same_key(session) -> None:
    from sqlalchemy.exc import IntegrityError

    table, destination = _seed_table(session)
    _seed_job(session, table, destination, status="queued")

    with pytest.raises(IntegrityError):
        _seed_job(session, table, destination, status="deferred")
    session.rollback()


def test_unique_index_exempts_forced_and_terminal_jobs(session) -> None:
    table, destination = _seed_table(session)
    _seed_job(session, table, destination, status="queued")
    _seed_job(session, table, destination, status="queued", dedupe_key_value=None)
    _seed_job(session, table, destination, status="succeeded")
    _seed_job(session, table, destination, status="succeeded")


def _record(
    job_id: str,
    *,
    status,
    mode="standard",
    run_created_at=None,
    dedupe_key_value="k",
    started_at=None,
    table_id="t1",
):
    from cryo.worker.worker import MaintenanceJobRecord

    return MaintenanceJobRecord(
        id=job_id,
        run_id=f"run-{job_id}",
        table_id=table_id,
        destination_id="dest-1",
        mode=mode,
        status=status,
        run_created_at=run_created_at,
        dedupe_key=dedupe_key_value,
        started_at=started_at,
    )


def test_worker_supersedes_queued_duplicates_and_drops_their_queue_entries() -> None:
    from cryo.domain.enums import JobStatus
    from cryo.queue.redis_queue import InMemoryMaintenanceQueue
    from cryo.worker.worker import InMemoryJobStore, Worker

    started = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    store = InMemoryJobStore()
    queue = InMemoryMaintenanceQueue()
    done = _record("done", status=JobStatus.SUCCEEDED, started_at=started)
    dup = _record(
        "dup", status=JobStatus.QUEUED, mode="light", run_created_at=started - timedelta(hours=1)
    )
    deeper = _record(
        "deeper", status=JobStatus.QUEUED, mode="deep", run_created_at=started - timedelta(hours=1)
    )
    forced = _record("forced", status=JobStatus.QUEUED, mode="light", dedupe_key_value=None)
    for record in (done, dup, deeper, forced):
        store.jobs[record.id] = record
    for record in (dup, deeper, forced):
        queue.enqueue(record.id, record.destination_id)
    worker = Worker(queue, store, lambda d: None, InMemoryLockStore())

    worker._supersede_covered_jobs(done)

    assert dup.status is JobStatus.SKIPPED
    assert dup.error == "superseded_by:done"
    assert deeper.status is JobStatus.QUEUED
    assert forced.status is JobStatus.QUEUED
    assert set(queue.pending("dest-1")) == {"deeper", "forced"}


def test_worker_supersede_never_raises_into_finalize() -> None:
    from cryo.queue.redis_queue import InMemoryMaintenanceQueue
    from cryo.worker.worker import InMemoryJobStore, Worker

    store = InMemoryJobStore()
    store.supersede_covered_jobs = lambda job_id: (_ for _ in ()).throw(RuntimeError("db down"))  # type: ignore[method-assign]
    worker = Worker(InMemoryMaintenanceQueue(), store, lambda d: None, InMemoryLockStore())

    worker._supersede_covered_jobs(_record("done", status="succeeded"))


def test_native_watcher_supersedes_through_its_session_and_queue_hook(session) -> None:
    from cryo.engines.native.watcher import NativeJobWatcher

    table, destination = _seed_table(session)
    started = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    done = _seed_job(session, table, destination, status="succeeded", started_at=started)
    dup = _seed_job(
        session,
        table,
        destination,
        status="queued",
        mode="light",
        created_at=started - timedelta(minutes=5),
    )
    cancelled: list[tuple[str, str | None]] = []
    watcher = NativeJobWatcher(
        lambda: session,
        InMemoryLockStore(),
        mark_status=lambda *a, **k: None,
        cancel_pending=lambda job_id, destination_id: cancelled.append((job_id, destination_id)),
    )

    watcher._supersede_covered(session, done)
    session.commit()

    session.refresh(dup)
    assert dup.status == "skipped"
    assert cancelled == [(str(dup.id), str(destination.id))]
