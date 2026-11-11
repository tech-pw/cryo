"""Async dispatch of Spark-on-K8s statements (#435): submit, park, resolve, resume."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest

from cryo.db.repositories.job_steps import InMemoryJobStepStore
from cryo.domain.enums import EngineType, JobStatus, JobStepStatus, Mode, OperationType
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.engines.spark.async_state import SparkAsyncState
from cryo.governor.async_spark_resolver import resolve_parked_spark_jobs
from cryo.queue.async_spark import InMemoryAsyncSparkRegistry
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.orphan_recovery import recover_orphaned_jobs
from cryo.worker.safety_gates import TableActivity
from cryo.worker.worker import (
    AsyncSparkDispatch,
    DestinationConfig,
    InMemoryJobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
    Worker,
)

_ORDER = (OperationType.OPTIMIZE, OperationType.EXPIRE_SNAPSHOTS)


class _AsyncSparkEngine:
    """Stands in for SparkK8sEngine: Spark Jobs live in ``jobs`` until a test ends them."""

    type = EngineType.TRINO

    def __init__(self, jobs: dict[str, str]) -> None:
        self.jobs = jobs
        self.submitted: list[tuple[OperationType, str]] = []
        self.blocking_calls: list[OperationType] = []

    def render(self, plan: object) -> list[RenderedStatement]:
        return [
            RenderedStatement(action=op, sql=f"CALL {op.value}", engine=self.type) for op in _ORDER
        ]

    def session_setup(self) -> list[str]:
        return []

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        self.blocking_calls.append(stmt.action)
        return ExecResult(ok=True, engine_ref="blocking", detail=stmt.sql, duration_ms=1)

    def execute_async(
        self,
        stmt: RenderedStatement,
        ctx: ExecCtx | None = None,
        state: SparkAsyncState | None = None,
    ) -> ExecResult | SparkAsyncState:
        if state is None or state.ref is None:
            ref = f"spark-{len(self.submitted) + 1}"
            self.submitted.append((stmt.action, ref))
            self.jobs[ref] = "RUNNING"
            if ctx is not None and ctx.on_engine_ref is not None:
                ctx.on_engine_ref(ref)
            now = time.time()
            return SparkAsyncState(started_at=now, ref=ref, submitted_at=now, deadline_seconds=7200)
        outcome = self.jobs[state.ref]
        if outcome == "RUNNING":
            return state
        if outcome == "SUCCESS":
            return ExecResult(
                ok=True,
                engine_ref=state.ref,
                detail=stmt.sql,
                duration_ms=7,
                input_files_count=4,
                output_files_count=1,
                actual_bytes_freed=1024,
            )
        return ExecResult(
            ok=False, engine_ref=state.ref, detail=stmt.sql, error="boom", duration_ms=7
        )


@dataclass
class _Harness:
    queue: InMemoryMaintenanceQueue = field(default_factory=InMemoryMaintenanceQueue)
    store: InMemoryJobStore = field(default_factory=InMemoryJobStore)
    steps: InMemoryJobStepStore = field(default_factory=InMemoryJobStepStore)
    locks: InMemoryLockStore = field(default_factory=InMemoryLockStore)
    registry: InMemoryAsyncSparkRegistry = field(default_factory=InMemoryAsyncSparkRegistry)
    jobs: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.engine = _AsyncSparkEngine(self.jobs)
        self.store.add_destination(DestinationConfig(id="dest", engine=EngineType.TRINO))

    def add_job(self, job_id: str = "job-1", table_id: str = "table-1") -> None:
        self.store.add_table_data(
            table_id,
            TableMaintenanceData(
                stats={
                    "catalog_name": "sample",
                    "schema_name": "s",
                    "table_name": table_id,
                    "total_files": 10,
                    "total_bytes": 0,
                    "small_files": 10,
                }
            ),
        )
        self.store.add_job(
            MaintenanceJobRecord(
                id=job_id,
                run_id="run-1",
                table_id=table_id,
                destination_id="dest",
                mode=Mode.STANDARD,
                status=JobStatus.QUEUED,
                operations=_ORDER,
            )
        )
        self.queue.enqueue(job_id, "dest")
        self.queue.admit(job_id, "dest", replica="r1")
        self.locks.acquire_table_lock(table_id, job_id, 3600)

    def worker(self, *, holder: str = "w1", max_in_flight: int = 4, enabled: bool = True) -> Worker:
        return Worker(
            self.queue,
            self.store,
            engine_factory=lambda _d: self.engine,
            lock_store=self.locks,
            step_ledger=self.steps,
            table_activity_probe=lambda: TableActivity.QUIET,
            quiet_interval_sec=0,
            quiet_max_wait_sec=None,
            async_spark=AsyncSparkDispatch(
                registry=self.registry,
                holder=holder,
                enabled=enabled,
                max_in_flight=max_in_flight,
                poll_interval_seconds=30,
            ),
        )

    def resolve(self, *, now: float | None = None) -> int:
        result = resolve_parked_spark_jobs(
            self.registry,
            self.queue,
            self.locks,
            self.store,
            is_terminal=lambda _dest, ref: self.jobs[ref] != "RUNNING",
            now=(time.time() + 3600) if now is None else now,
        )
        return result.handed_back


@pytest.fixture(autouse=True)
def _no_abac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cryo.security.abac_enforcement.evaluate_maintenance_allowed",
        lambda *_a, **_k: (True, None),
    )


def test_worker_submits_and_moves_on_holding_slot_and_lock() -> None:
    h = _Harness()
    h.add_job("job-1", "table-1")
    h.add_job("job-2", "table-2")
    worker = h.worker()

    first = worker.process_next()
    second = worker.process_next()

    assert first is not None and second is not None
    # Both Spark Jobs are in flight from one worker; neither job is finished.
    assert [ref for _, ref in h.engine.submitted] == ["spark-1", "spark-2"]
    assert h.store.get_job("job-1").status is JobStatus.RUNNING
    assert h.store.get_job("job-2").status is JobStatus.RUNNING
    assert h.locks.table_lock_holder("table-1") == "job-1"
    assert h.locks.table_lock_holder("table-2") == "job-2"
    assert h.queue.status("job-1") is JobStatus.RUNNING
    assert h.steps.get_step("job-1", 0).status is JobStepStatus.STARTED
    assert h.steps.get_step("job-1", 0).engine_ref == "spark-1"
    assert h.registry.owned_count("w1") == 2
    assert h.store.audit == []
    assert h.engine.blocking_calls == []


def test_running_spark_job_is_not_handed_back_and_lock_stays() -> None:
    h = _Harness()
    h.add_job()
    h.worker().process_next()

    assert h.resolve() == 0
    assert h.queue.claim_admitted() is None
    assert h.locks.table_lock_holder("table-1") == "job-1"
    assert h.registry.is_parked("job-1")


def test_multi_step_job_runs_steps_in_order_only_after_each_resolves() -> None:
    h = _Harness()
    h.add_job()
    worker = h.worker()

    worker.process_next()
    assert h.engine.submitted == [(OperationType.OPTIMIZE, "spark-1")]

    h.jobs["spark-1"] = "SUCCESS"
    assert h.resolve() == 1
    resumed = worker.process_next()

    # optimize was recorded, and only then was expire_snapshots submitted.
    assert resumed is not None and resumed.status is JobStatus.RUNNING
    assert h.engine.submitted == [
        (OperationType.OPTIMIZE, "spark-1"),
        (OperationType.EXPIRE_SNAPSHOTS, "spark-2"),
    ]
    assert h.steps.get_step("job-1", 0).status is JobStepStatus.SUCCEEDED
    assert h.steps.get_step("job-1", 1).status is JobStepStatus.STARTED
    assert [a.action for a in h.store.audit] == [OperationType.OPTIMIZE]
    assert h.locks.table_lock_holder("table-1") == "job-1"

    h.jobs["spark-2"] = "SUCCESS"
    assert h.resolve() == 1
    done = worker.process_next()

    assert done is not None and done.status is JobStatus.SUCCEEDED
    assert [a.action for a in h.store.audit] == list(_ORDER)
    assert h.locks.table_lock_holder("table-1") is None
    assert h.queue.status("job-1") is JobStatus.SUCCEEDED
    assert not h.registry.is_parked("job-1")


def test_resolution_success_records_audit_and_ledger_like_sync_path() -> None:
    h = _Harness()
    h.add_job()
    worker = h.worker()
    worker.process_next()
    h.jobs["spark-1"] = "SUCCESS"
    h.resolve()
    worker.process_next()

    audit = h.store.audit[0]
    assert audit.status == "OK"
    assert audit.input_files_count == 4
    assert audit.output_files_count == 1
    assert audit.actual_bytes_freed == 1024
    assert audit.duration_ms == 7
    step = h.steps.get_step("job-1", 0)
    assert step.status is JobStepStatus.SUCCEEDED
    assert step.engine_ref == "spark-1"


def test_resolution_failure_continues_to_next_op_and_finishes_partial() -> None:
    h = _Harness()
    h.add_job()
    worker = h.worker()
    worker.process_next()
    h.jobs["spark-1"] = "FAILED"
    h.resolve()
    worker.process_next()

    assert h.steps.get_step("job-1", 0).status is JobStepStatus.FAILED
    assert h.store.audit[0].status == "FAILED"
    assert h.store.audit[0].detail == "boom"

    h.jobs["spark-2"] = "SUCCESS"
    h.resolve()
    done = worker.process_next()

    # The failed optimize is not re-run, and the job is PARTIAL exactly as the blocking
    # loop would report it.
    assert [op for op, _ in h.engine.submitted] == list(_ORDER)
    assert done is not None and done.status is JobStatus.PARTIAL
    assert done.error == "optimize: boom"
    assert h.locks.table_lock_holder("table-1") is None


def test_cap_reached_falls_back_to_blocking() -> None:
    h = _Harness()
    h.add_job("job-1", "table-1")
    h.add_job("job-2", "table-2")
    worker = h.worker(max_in_flight=1)

    worker.process_next()
    second = worker.process_next()

    assert h.engine.submitted == [(OperationType.OPTIMIZE, "spark-1")]
    assert second is not None and second.status is JobStatus.SUCCEEDED
    assert h.engine.blocking_calls == list(_ORDER)


def test_disabled_runs_blocking_path() -> None:
    h = _Harness()
    h.add_job()
    done = h.worker(enabled=False).process_next()

    assert done is not None and done.status is JobStatus.SUCCEEDED
    assert h.engine.submitted == []
    assert h.engine.blocking_calls == list(_ORDER)


def test_restart_resumes_on_another_worker_without_resubmitting() -> None:
    h = _Harness()
    h.add_job()
    h.worker(holder="w1").process_next()

    # w1 is gone; a fresh worker (new pod) picks the hand-back up from Redis state.
    h.jobs["spark-1"] = "SUCCESS"
    h.resolve()
    h.worker(holder="w2").process_next()

    assert h.engine.submitted == [
        (OperationType.OPTIMIZE, "spark-1"),
        (OperationType.EXPIRE_SNAPSHOTS, "spark-2"),
    ]
    assert len(h.store.audit) == 1
    assert h.registry.get("job-1").owner == "w2"
    assert h.registry.owned_count("w1") == 0


def test_resolver_is_idempotent_and_hands_back_once() -> None:
    h = _Harness()
    h.add_job()
    h.worker().process_next()
    h.jobs["spark-1"] = "SUCCESS"
    now = time.time() + 3600

    assert h.resolve(now=now) == 1
    # A second tick (or a second governor replica) before the hand-back is claimed.
    assert h.resolve(now=now + 1) == 0
    assert h.queue.claim_admitted() == "job-1"
    assert h.queue.claim_admitted() is None


def test_lost_resume_is_handed_back_again_after_grace() -> None:
    h = _Harness()
    h.add_job()
    h.worker().process_next()
    h.jobs["spark-1"] = "SUCCESS"
    now = time.time() + 3600
    h.resolve(now=now)
    # The worker that claimed the hand-back died after acking it.
    assert h.queue.claim_admitted() == "job-1"
    h.queue.mark_running("job-1")

    assert h.resolve(now=now + 60) == 0
    assert h.resolve(now=now + 601) == 1
    done = h.worker(holder="w2")
    done.process_next()
    assert [a.action for a in h.store.audit] == [OperationType.OPTIMIZE]


def test_duplicate_hand_back_while_resuming_does_nothing() -> None:
    h = _Harness()
    h.add_job()
    h.worker().process_next()
    h.jobs["spark-1"] = "SUCCESS"
    h.resolve()
    h.locks.acquire_job_owner("job-1", "other-worker", 60)

    h.worker(holder="w2").process_next()

    assert h.store.audit == []
    assert h.registry.is_parked("job-1")


def test_orphan_recovery_leaves_parked_jobs_alone() -> None:
    h = _Harness()
    h.add_job()
    h.worker().process_next()
    h.jobs["spark-1"] = "SUCCESS"
    h.store.get_job("job-1").status = JobStatus.RUNNING

    result = recover_orphaned_jobs(
        h.store,
        h.queue,
        h.locks,
        h.steps,
        holder_id="w9",
        is_async_parked=h.registry.is_parked,
    )

    assert result.adopted == 0
    assert h.steps.get_step("job-1", 0).status is JobStepStatus.STARTED


def test_resolver_drops_entries_for_jobs_that_ended() -> None:
    h = _Harness()
    h.add_job()
    h.worker().process_next()
    h.store.get_job("job-1").status = JobStatus.FAILED

    result = resolve_parked_spark_jobs(
        h.registry,
        h.queue,
        h.locks,
        h.store,
        is_terminal=lambda _d, _r: False,
        now=time.time() + 3600,
    )

    assert result.dropped == 1
    assert not h.registry.is_parked("job-1")


def test_resolver_bounded_by_limit() -> None:
    h = _Harness()
    for i in range(3):
        h.add_job(f"job-{i}", f"table-{i}")
    worker = h.worker()
    for _ in range(3):
        worker.process_next()

    result = resolve_parked_spark_jobs(
        h.registry,
        h.queue,
        h.locks,
        h.store,
        is_terminal=lambda _d, _r: True,
        now=time.time() + 3600,
        limit=2,
    )

    assert result.examined == 2
    assert result.handed_back == 2


def test_spark_async_state_round_trips() -> None:
    state = SparkAsyncState(
        started_at=1.0, ref="r", submitted_at=2.0, deadline_seconds=60, attempt=1, oom_steps=1
    )
    assert SparkAsyncState.from_dict(state.to_dict()) == state
    assert state.ready_to_resume(now=61.9, run_terminal=False) is False
    assert state.ready_to_resume(now=62.0, run_terminal=False) is True
