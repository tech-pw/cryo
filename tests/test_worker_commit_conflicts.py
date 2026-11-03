"""Conflict-aware retry for maintenance commits that lose to a concurrent writer (#77)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

import cryo.config
from cryo.domain.enums import JobStatus, OperationType
from cryo.engines.base import (
    COMMIT_CONFLICT_GIVE_UP_ACTION,
    COMMIT_CONFLICT_MARKER,
    ExecCtx,
    RenderedStatement,
    is_commit_conflict,
)
from cryo.engines.base import ExecResult as BaseExecResult
from cryo.observability.metrics import worker_commit_conflicts_total
from cryo.worker.worker import AuditRecord, FakeMaintenanceEngine, InMemoryJobStore, Worker
from tests.test_worker import _worker_fixture

SPARK_CONFLICT = (
    "org.apache.iceberg.exceptions.ValidationException: Found conflicting files that can "
    'contain records matching ref(name="day") == 20391: [s3://bucket/data/part-1.parquet]'
)
JAVA_CONFLICT = (
    "org.apache.iceberg.exceptions.CommitFailedException: Cannot commit changes based on stale "
    "table metadata"
)
REST_CONFLICT = (
    "CommitFailedException: Requirement failed: branch main has changed: expected id "
    "8812734 != 9912311"
)
TRINO_CONFLICT = "ICEBERG_COMMIT_ERROR: Failed to commit Iceberg update to table: analytics.events"


@pytest.fixture(autouse=True)
def _fast_conflict_settings(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    overrides: dict[str, Any] = {
        "worker_commit_conflict_retries": 1,
        "worker_commit_conflict_retry_backoff_seconds": 0.0,
        "worker_commit_conflict_max_deferrals": 3,
    }
    real = cryo.config.get_settings()
    monkeypatch.setattr(
        cryo.config, "get_settings", lambda: real.model_copy(update=dict(overrides))
    )
    return overrides


class _ConflictingEngine(FakeMaintenanceEngine):
    """Loses the optimize commit ``conflicts`` times, then commits normally."""

    def __init__(self, conflicts: int, *, message: str = SPARK_CONFLICT, raise_: bool = False):
        super().__init__()
        self.remaining = conflicts
        self.message = message
        self.raise_ = raise_
        self.attempts: list[OperationType] = []

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> BaseExecResult:
        self.attempts.append(stmt.action)
        if stmt.action is OperationType.OPTIMIZE and self.remaining > 0:
            self.remaining -= 1
            if self.raise_:
                raise RuntimeError(self.message)
            return BaseExecResult(ok=False, error=self.message)
        return super().execute(stmt, ctx)


def _counter(outcome: str) -> float:
    return worker_commit_conflicts_total.labels(outcome=outcome)._value.get()


def _readmit(queue: Any, store: InMemoryJobStore) -> None:
    store.mark_status("job-1", JobStatus.ADMITTED)
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", "r1")


@pytest.mark.parametrize("message", [SPARK_CONFLICT, JAVA_CONFLICT, REST_CONFLICT, TRINO_CONFLICT])
def test_engine_conflict_messages_are_recognised(message: str) -> None:
    assert is_commit_conflict(message)
    assert is_commit_conflict(f"{COMMIT_CONFLICT_MARKER} anything")


@pytest.mark.parametrize(
    "message",
    [
        None,
        "",
        "org.apache.iceberg.exceptions.CommitStateUnknownException: commit conflict unknown",
        "NoSuchTableException: Table does not exist: analytics.events",
        "Query exceeded per-node memory limit",
    ],
)
def test_other_failures_are_not_conflicts(message: str | None) -> None:
    assert not is_commit_conflict(message)


def test_one_lost_commit_is_retried_in_place_and_the_job_succeeds() -> None:
    queue, locks, store = _worker_fixture()
    engine = _ConflictingEngine(conflicts=1)
    retried = _counter("retried")

    result = Worker(queue, store, lambda _d: engine, locks).process_next()

    assert result is not None
    assert result.status is JobStatus.SUCCEEDED
    assert engine.attempts.count(OperationType.OPTIMIZE) == 2
    assert _counter("retried") == retried + 1
    assert all(record.status == "OK" for record in store.audit)


def test_a_raised_conflict_is_retried_in_place_too() -> None:
    queue, locks, store = _worker_fixture()
    engine = _ConflictingEngine(conflicts=1, message=JAVA_CONFLICT, raise_=True)

    result = Worker(queue, store, lambda _d: engine, locks).process_next()

    assert result is not None
    assert result.status is JobStatus.SUCCEEDED
    assert engine.attempts.count(OperationType.OPTIMIZE) == 2


def test_persistent_conflicts_defer_and_replan_then_give_up_after_the_limit() -> None:
    queue, locks, store = _worker_fixture()
    # Each run makes 2 attempts (1 + 1 in-place retry); 8 losses outlast 4 runs.
    engine = _ConflictingEngine(conflicts=8)
    worker = Worker(queue, store, lambda _d: engine, locks)
    plans_saved: list[str] = []
    original_save = store.save_plan

    def _save_plan(job_id: str, plan: dict[str, Any]) -> None:
        plans_saved.append(job_id)
        original_save(job_id, plan)

    store.save_plan = _save_plan  # type: ignore[method-assign]
    deferred = _counter("deferred")
    gave_up = _counter("gave_up")

    statuses: list[JobStatus] = []
    errors: list[str] = []
    for run in range(4):
        if run:
            _readmit(queue, store)
        result = worker.process_next()
        assert result is not None
        statuses.append(result.status)
        errors.append(result.error or "")
        assert not locks.is_table_locked("table-1")

    assert statuses[:3] == [JobStatus.DEFERRED] * 3
    assert [error.split(":", 2)[:2] for error in errors[:3]] == [
        ["commit_conflict", " 1 of 3"],
        ["commit_conflict", " 2 of 3"],
        ["commit_conflict", " 3 of 3"],
    ]
    # Other operations still landed, so the give-up is PARTIAL rather than FAILED.
    assert statuses[3] is JobStatus.PARTIAL
    assert errors[3].startswith("commit_conflict_give_up: 4 conflicts: optimize:")
    # Every run built a fresh plan from the table's current stats.
    assert plans_saved == ["job-1"] * 4
    assert _counter("deferred") == deferred + 3
    assert _counter("gave_up") == gave_up + 1
    conflict_audits = [
        record for record in store.audit if (record.detail or "").startswith(COMMIT_CONFLICT_MARKER)
    ]
    assert len(conflict_audits) == 4
    assert {record.status for record in conflict_audits} == {"FAILED"}
    assert [event["action"] for event in store.events] == [COMMIT_CONFLICT_GIVE_UP_ACTION]


def test_a_real_failure_next_to_a_conflict_is_not_deferred() -> None:
    queue, locks, store = _worker_fixture()

    class _Mixed(FakeMaintenanceEngine):
        def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> BaseExecResult:
            if stmt.action is OperationType.OPTIMIZE:
                return BaseExecResult(ok=False, error=SPARK_CONFLICT)
            if stmt.action is OperationType.EXPIRE_SNAPSHOTS:
                return BaseExecResult(ok=False, error="Query exceeded per-node memory limit")
            return super().execute(stmt, ctx)

    result = Worker(queue, store, lambda _d: _Mixed(), locks).process_next()

    assert result is not None
    assert result.status in {JobStatus.PARTIAL, JobStatus.FAILED}
    assert not (result.error or "").startswith("commit_conflict")


def test_zero_max_deferrals_fails_on_the_first_surviving_conflict(
    _fast_conflict_settings: dict[str, Any],
) -> None:
    _fast_conflict_settings["worker_commit_conflict_max_deferrals"] = 0
    _fast_conflict_settings["worker_commit_conflict_retries"] = 0
    queue, locks, store = _worker_fixture()
    engine = _ConflictingEngine(conflicts=1)

    result = Worker(queue, store, lambda _d: engine, locks).process_next()

    assert result is not None
    assert (result.error or "").startswith("commit_conflict_give_up: 1 conflicts")
    assert engine.attempts.count(OperationType.OPTIMIZE) == 1


def test_a_store_without_conflict_counting_never_defers_forever() -> None:
    queue, locks, store = _worker_fixture()

    class _NoCounting(InMemoryJobStore):
        count_commit_conflicts = None  # type: ignore[assignment]

    bare = _NoCounting()
    bare.__dict__.update(store.__dict__)
    result = Worker(queue, bare, lambda _d: _ConflictingEngine(conflicts=10), locks).process_next()

    assert result is not None
    assert result.status is JobStatus.PARTIAL
    assert (result.error or "").startswith("commit_conflict_give_up")


def test_sql_store_counts_marked_conflicts_and_records_the_give_up(session_factory: Any) -> None:
    from cryo.db.models import AuditLog
    from cryo.db.repositories.job_store import SQLJobStore
    from cryo.domain.enums import EngineType
    from tests.test_events_live_probe import _add_source, _add_table

    with session_factory() as session:
        source_id = _add_source(session)
        table_id = _add_table(session, source_id, "events")
        session.commit()
    job_id = str(uuid.uuid4())
    store = SQLJobStore(session_factory)
    for detail in (f"{COMMIT_CONFLICT_MARKER} {SPARK_CONFLICT}", "plain failure", None):
        store.record_audit(
            AuditRecord(
                job_id=job_id,
                table_id=str(table_id),
                action=OperationType.OPTIMIZE,
                status="FAILED",
                engine=EngineType.SPARK_EMR,
                detail=detail,
            )
        )
    assert store.count_commit_conflicts(job_id) == 1
    assert store.count_commit_conflicts(str(uuid.uuid4())) == 0

    store.record_job_event(
        job_id=job_id,
        table_id=str(table_id),
        action=COMMIT_CONFLICT_GIVE_UP_ACTION,
        status="FAILED",
        detail="commit_conflict_give_up: 4 conflicts",
        actor="system:worker",
    )
    with session_factory() as session:
        rows = session.query(AuditLog).filter(AuditLog.action == COMMIT_CONFLICT_GIVE_UP_ACTION)
        assert [(row.status, row.engine) for row in rows] == [("FAILED", "worker")]
