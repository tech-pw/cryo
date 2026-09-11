from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol, cast
from uuid import UUID

from cryo.db.repositories.intents import MaintenancePausedError
from cryo.domain.enums import EngineType, JobStatus, JobStepStatus, Mode, OperationType
from cryo.domain.intents import (
    AttemptStatus,
    EffectBudgetUsage,
    EffectState,
    MaintenanceIntentRecord,
)
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.base import ExecCtx, RenderedStatement, is_table_not_found_error
from cryo.engines.base import ExecResult as BaseExecResult
from cryo.engines.native.routing import (
    native_fallback_mismatch_detail,
    native_fallback_supports,
)
from cryo.intents.validation import (
    IntentValidationError,
    validate_effect_budget,
    validate_execution_intent,
)
from cryo.observability.correlation import correlation_scope, set_context
from cryo.observability.metrics import worker_job_duration_seconds, worker_job_executions_total
from cryo.observability.tracing import trace_operation
from cryo.planner.execution import build_execution_plan, skip_operations_from_preset
from cryo.planner.serialization import plan_to_dict
from cryo.queue.locks import LockStore
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.queue.run_events import (
    RunEventSink,
    build_job_status,
    build_log_line,
    safe_publish,
)
from cryo.worker.safety_gates import requires_quiet_window, wait_for_table_quiet

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DestinationConfig:
    id: str
    engine: EngineType | str = EngineType.TRINO
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class MaintenanceJobRecord:
    id: str
    run_id: str
    table_id: str
    destination_id: str
    mode: Mode | str
    params: Mapping[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.QUEUED
    plan: dict[str, Any] | None = None
    engine_ref: str | None = None
    replica: str | None = None
    # Stamped when the job transitions to ADMITTED -- see MaintenanceJob.admitted_at's own
    # docstring. Used by reconcile_stuck_admitted_jobs to age out jobs the Worker's at-most-
    # once admit handoff (Redis rpush/lpop, no ack) silently dropped.
    admitted_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    deadline_at: datetime | None = None
    cooldown_hours: int = 0
    last_maintained_at: datetime | None = None
    actor: str | None = None
    operations: tuple[OperationType, ...] | None = None
    force_admit: bool = False
    dispatch_id: str | None = None
    intent_id: str | None = None
    intent_status: str | None = None
    intent_expires_at: datetime | None = None
    fencing_epoch: int | None = None
    tenant_id: str | None = None
    intent_effect_destination_ids: tuple[str, ...] = ()
    correlation_id: str | None = None
    # Live-verified 2026-09-03: reconcile_deferred_jobs (queue/reconcile.py) has no way to
    # tell a job's parent MaintenanceRun already reached a terminal status (finalize_open_runs
    # already decided the run is done) from one still genuinely in flight -- a job that's
    # permanently unadmittable (its run's deadline_at long passed, the run itself already
    # 'partial'/'failed'/'succeeded') gets re-deferred forever instead of ever failing. Because
    # a re-defer doesn't refresh finished_at (reconcile_deferred_jobs's own "continue" on a
    # still-deferred decision), these permanently-stuck jobs stay the *oldest* deferred jobs
    # and, combined with list_deferred_jobs's oldest-first bounded ordering, monopolized the
    # entire per-wave budget: confirmed live, 31,902 of 40,785 deferred jobs belonged to
    # already-terminal runs, and 5+ consecutive waves admitted/deferred=0 because the same
    # dead jobs were re-picked every time. None by default -- optional so callers that don't
    # need it (most) don't pay for the extra query.
    run_status: str | None = None


@dataclass(frozen=True)
class TableMaintenanceData:
    stats: Mapping[str, Any]
    partitions: Sequence[Mapping[str, Any]] = ()
    source_config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditRecord:
    job_id: str
    table_id: str
    action: OperationType
    status: str
    engine: EngineType | str
    detail: str | None = None
    duration_ms: int | None = None
    input_files_count: int | None = None
    output_files_count: int | None = None
    actual_bytes_freed: int | None = None
    snapshots_expired: int | None = None
    manifests_rewritten: int | None = None
    actor: str | None = None
    # Async fallback-dispatch tracking (PDAE-8352) -- see AuditLog.engine_ref's docstring.
    engine_ref: str | None = None


@dataclass(frozen=True)
class ExecContext:
    job_id: str
    table_id: str
    destination_id: str
    statement_index: int
    replica: str | None = None


@dataclass(frozen=True)
class ExecResult:
    ok: bool = True
    engine_ref: str | None = None
    detail: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    actual_bytes_freed: int | None = None


class ExecutableMaintenanceEngine(Protocol):
    """Protocol for engines the worker can drive end-to-end.

    Today this is identical to ``cryo.engines.base.MaintenanceEngine``; the
    alias is kept so worker code does not import from ``cryo.engines.base``
    directly and so future divergence (e.g. engines without probe_capacity) is
    easy to express here.
    """

    type: EngineType

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]: ...

    def session_setup(self) -> list[str]: ...

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> BaseExecResult: ...

    def execute_plan(
        self, plan: MaintenancePlan, ctx: ExecCtx | None = None
    ) -> list[BaseExecResult]: ...


class JobStore(Protocol):
    def get_job(self, job_id: str) -> MaintenanceJobRecord: ...

    def get_destination(self, destination_id: str) -> DestinationConfig: ...

    def get_table_data(self, table_id: str) -> TableMaintenanceData: ...

    def save_plan(self, job_id: str, plan: dict[str, Any]) -> None: ...

    def mark_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        engine_ref: str | None = None,
        replica: str | None = None,
        error: str | None = None,
    ) -> None: ...

    def record_audit(self, record: AuditRecord) -> None: ...

    def pending_jobs(self, destination_id: str) -> list[MaintenanceJobRecord]: ...

    def set_force_admit(self, job_id: str, value: bool) -> None: ...

    def set_engine_ref(self, job_id: str, engine_ref: str) -> None: ...

    def deactivate_table(self, table_id: str, reason: str) -> None: ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, MaintenanceJobRecord] = {}
        self.destinations: dict[str, DestinationConfig] = {}
        self.tables: dict[str, TableMaintenanceData] = {}
        self.audit: list[AuditRecord] = []
        self.deactivated_tables: dict[str, str] = {}

    def add_destination(self, destination: DestinationConfig) -> None:
        self.destinations[destination.id] = destination

    def add_job(self, job: MaintenanceJobRecord) -> None:
        self.jobs[job.id] = job

    def add_table_data(self, table_id: str, data: TableMaintenanceData) -> None:
        self.tables[table_id] = data

    def get_job(self, job_id: str) -> MaintenanceJobRecord:
        return self.jobs[job_id]

    def get_destination(self, destination_id: str) -> DestinationConfig:
        destination = self.try_get_destination(destination_id)
        if destination is None:
            raise KeyError(f"destination not found: {destination_id}")
        return destination

    def try_get_destination(self, destination_id: str) -> DestinationConfig | None:
        return self.destinations.get(destination_id)

    def get_table_data(self, table_id: str) -> TableMaintenanceData:
        return self.tables[table_id]

    def save_plan(self, job_id: str, plan: dict[str, Any]) -> None:
        self.jobs[job_id].plan = plan

    def mark_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        engine_ref: str | None = None,
        replica: str | None = None,
        error: str | None = None,
    ) -> None:
        job = self.jobs[job_id]
        job.status = status
        if status is JobStatus.QUEUED:
            job.started_at = None
            job.finished_at = None
            job.admitted_at = None
        if status in {JobStatus.QUEUED, JobStatus.ADMITTED, JobStatus.RUNNING}:
            job.finished_at = None
        if status is JobStatus.ADMITTED:
            job.admitted_at = datetime.now(UTC)
        if status is JobStatus.RUNNING:
            job.started_at = datetime.now(UTC)
        if status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.SKIPPED,
            JobStatus.DEFERRED,
        }:
            job.finished_at = datetime.now(UTC)
        if engine_ref is not None:
            job.engine_ref = engine_ref
        if replica is not None:
            job.replica = replica
        job.error = error

    def record_audit(self, record: AuditRecord) -> None:
        self.audit.append(record)

    def set_force_admit(self, job_id: str, value: bool) -> None:
        self.jobs[job_id].force_admit = value

    def set_engine_ref(self, job_id: str, engine_ref: str) -> None:
        self.jobs[job_id].engine_ref = engine_ref

    def deactivate_table(self, table_id: str, reason: str) -> None:
        self.deactivated_tables[table_id] = reason

    def pending_jobs(self, destination_id: str) -> list[MaintenanceJobRecord]:
        return [
            job
            for job in self.jobs.values()
            if job.destination_id == destination_id and job.status is JobStatus.QUEUED
        ]

    def list_queued_jobs(self, *, limit: int | None = None) -> list[MaintenanceJobRecord]:
        queued = [job for job in self.jobs.values() if job.status is JobStatus.QUEUED]
        if limit is not None:
            return queued[:limit]
        return queued

    def list_deferred_jobs(self, *, limit: int | None = None) -> list[MaintenanceJobRecord]:
        deferred = [job for job in self.jobs.values() if job.status is JobStatus.DEFERRED]
        if limit is not None:
            return deferred[:limit]
        return deferred

    def list_running_jobs(self, *, limit: int | None = None) -> list[MaintenanceJobRecord]:
        running = [job for job in self.jobs.values() if job.status is JobStatus.RUNNING]
        if limit is not None:
            return running[:limit]
        return running

    def list_admitted_jobs(self, *, limit: int | None = None) -> list[MaintenanceJobRecord]:
        admitted = [job for job in self.jobs.values() if job.status is JobStatus.ADMITTED]
        if limit is not None:
            return admitted[:limit]
        return admitted

    def destination_exists(self, destination_id: str) -> bool:
        destination = self.destinations.get(destination_id)
        return destination is not None


class FakeMaintenanceEngine:
    """Executable test engine used until concrete engine adapters are wired into workers."""

    def __init__(
        self,
        engine_type: EngineType | str = EngineType.TRINO,
        *,
        fail_actions: set[OperationType] | None = None,
        skip_actions: set[OperationType] | None = None,
        bytes_freed_by_action: dict[OperationType, int] | None = None,
    ) -> None:
        self.type = engine_type
        self.fail_actions = fail_actions or set()
        self.skip_actions = skip_actions or set()
        self.bytes_freed_by_action = bytes_freed_by_action or {}
        self.executed: list[RenderedStatement] = []

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]:
        return [
            RenderedStatement(
                action=operation.type,
                sql=f"FAKE {operation.type.value} {plan.table_ref}",
                engine=EngineType(self.type),
            )
            for operation in plan.operations
        ]

    def session_setup(self) -> list[str]:
        return []

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> BaseExecResult:
        if stmt.action in self.fail_actions:
            raise RuntimeError(f"fake engine failed {stmt.action.value}")
        self.executed.append(stmt)
        job_id = str(ctx.job_id)[:8] if ctx and ctx.job_id else "noctx"
        if stmt.action in self.skip_actions:
            return BaseExecResult(
                ok=True,
                skipped=True,
                engine_ref=f"fake-{job_id}-{len(self.executed)}",
                detail=f"skipped {stmt.action.value}",
            )
        return BaseExecResult(
            ok=True,
            engine_ref=f"fake-{job_id}-{len(self.executed)}",
            detail=stmt.sql,
            actual_bytes_freed=self.bytes_freed_by_action.get(stmt.action),
        )


EngineFactory = Callable[[DestinationConfig], ExecutableMaintenanceEngine]


class Worker:
    def __init__(
        self,
        queue: MaintenanceQueue,
        store: JobStore,
        engine_factory: EngineFactory | None = None,
        lock_store: LockStore | None = None,
        *,
        on_job_finished: Callable[[], None] | None = None,
        lock_ttl_seconds: int = 6 * 60 * 60,
        run_events: RunEventSink | None = None,
        step_ledger: Any | None = None,
        intent_store: Any | None = None,
        should_stop: Callable[[], bool] | None = None,
        table_activity_probe: Callable[..., Any] | None = None,
        quiet_interval_sec: float = 1.0,
        quiet_max_wait_sec: float | None = 120.0,
    ) -> None:
        self._queue = queue
        self._store = store
        self._engine_factory = engine_factory or default_engine_factory
        self._lock_store = lock_store
        self._on_job_finished = on_job_finished
        self._lock_ttl_seconds = lock_ttl_seconds
        self._run_events = run_events
        self._step_ledger = step_ledger
        self._intent_store: Any = intent_store
        self._should_stop = should_stop
        self._table_activity_probe = table_activity_probe
        self._quiet_interval_sec = quiet_interval_sec
        self._quiet_max_wait_sec = quiet_max_wait_sec

    def process_next(self) -> MaintenanceJobRecord | None:
        job_id = self._queue.claim_admitted()
        if job_id is None:
            return None

        job_start = perf_counter()
        job = self._store.get_job(job_id)
        with (
            correlation_scope(
                clear=True,
                correlation_id=job.correlation_id or job.id,
                run_id=job.run_id,
                job_id=job.id,
                intent_id=job.intent_id,
                table_id=job.table_id,
                destination_id=job.destination_id,
            ),
            trace_operation(
                "maintenance.job.execute",
                attributes={
                    "cryo.run.id": job.run_id,
                    "cryo.job.id": job.id,
                    "cryo.intent.id": job.intent_id,
                    "cryo.table.id": job.table_id,
                    "cryo.destination.id": job.destination_id,
                },
            ),
        ):
            return self._process_claimed_job(job, job_start=job_start)

    def _process_claimed_job(
        self,
        job: MaintenanceJobRecord,
        *,
        job_start: float,
    ) -> MaintenanceJobRecord:
        destination = _try_get_destination(self._store, job.destination_id)
        if destination is None:
            self._store.mark_status(job.id, JobStatus.FAILED, error="destination_not_found")
            self._queue.complete(job.id, JobStatus.FAILED)
            self._record_job_metrics(
                destination_id=job.destination_id,
                operation_type=_job_operation_type(job),
                status=JobStatus.FAILED,
                started_at=job_start,
            )
            if self._on_job_finished is not None:
                self._on_job_finished()
            return self._store.get_job(job.id)
        if not self._ensure_table_lock(job):
            self._store.mark_status(job.id, JobStatus.DEFERRED, error="table_locked")
            self._queue.defer(job.id)
            return self._store.get_job(job.id)
        self._queue.mark_running(job.id)
        self._store.mark_status(job.id, JobStatus.RUNNING)
        table_ref: str | None = None

        final_status = JobStatus.SUCCEEDED
        error: str | None = None
        engine_ref: str | None = None
        had_success = False
        had_skip = False
        active_intent: MaintenanceIntentRecord | None = None
        attempt_id: str | None = None
        reconciliation_required = False
        effect_budget_usage = EffectBudgetUsage()
        try:
            table_data = self._store.get_table_data(job.table_id)
            set_context(snapshot_id=table_data.stats.get("snapshot_watermark"))
            table_ref = _table_ref_from_stats(table_data.stats)
            safe_publish(
                self._run_events,
                job.run_id,
                build_job_status(
                    run_id=job.run_id,
                    job_id=job.id,
                    status=JobStatus.RUNNING.value,
                    table_id=job.table_id,
                    table_ref=table_ref,
                    message="Job started",
                ),
            )
            safe_publish(
                self._run_events,
                job.run_id,
                build_log_line(
                    run_id=job.run_id,
                    job_id=job.id,
                    table_id=job.table_id,
                    table_ref=table_ref,
                    message=f"Started maintenance job for {table_ref or job.table_id}",
                ),
            )
            if job.intent_id is not None:
                if self._intent_store is None:
                    raise RuntimeError("durable intent store is unavailable")
                active_intent = self._intent_store.get(job.intent_id)
                if active_intent is None:
                    raise RuntimeError(f"durable intent {job.intent_id} not found")
                effect_budget_usage = _load_effect_budget_usage(
                    self._intent_store,
                    active_intent,
                )
                plan = validate_execution_intent(
                    active_intent,
                    job=job,
                    destination_engine=destination.engine,
                    stats=dict(table_data.stats),
                )
                attempt = self._intent_store.begin_attempt(
                    job.intent_id,
                    worker_id=job.replica or "worker",
                )
                attempt_id = str(attempt.id)
                set_context(
                    attempt_id=attempt_id,
                    snapshot_id=active_intent.observed_snapshot_id,
                )
            else:
                plan = build_execution_plan(
                    stats=table_data.stats,
                    partitions=table_data.partitions,
                    mode=job.mode,
                    params=job.params,
                    destination=destination,
                    operations=job.operations,
                    protected_snapshot_ids=_protected_snapshot_ids(job.table_id),
                )
                self._store.save_plan(job.id, plan_to_dict(plan))

            engine = self._engine_factory(destination)
            if hasattr(engine, "with_source_config") and table_data.source_config:
                engine = engine.with_source_config(table_data.source_config)
            if hasattr(engine, "with_table_stats"):
                # Was gated to NATIVE only -- SparkK8sEngine.with_table_stats also exists now
                # (table-size-aware executor sizing, see its docstring), so any engine that
                # defines the hook should get it, matching with_source_config's own pattern
                # just above.
                engine = engine.with_table_stats(table_data.stats)

            if engine.type is EngineType.SPARK_EMR:
                _validate_spark_worker_plan(
                    plan,
                    table_data=table_data,
                    table_ref=table_ref,
                )

            native_routing = None
            if engine.type is EngineType.NATIVE:
                from cryo.engines.native.routing import resolve_native_routing

                native_routing = resolve_native_routing(plan, table_data.stats, destination.config)

            statements = engine.render(plan)
            safe_publish(
                self._run_events,
                job.run_id,
                build_log_line(
                    run_id=job.run_id,
                    job_id=job.id,
                    table_id=job.table_id,
                    table_ref=table_ref,
                    engine=str(engine.type),
                    message=(
                        f"Built plan with {len(plan.operations)} operations "
                        f"and {len(statements)} statements"
                    ),
                ),
            )
            if not statements and (native_routing is None or not native_routing.has_work):
                final_status = JobStatus.SKIPPED
            elif engine.type is EngineType.NATIVE and hasattr(engine, "execute_plan"):
                (
                    native_status,
                    native_error,
                    native_engine_ref,
                    native_had_success,
                    native_had_skip,
                    native_reconciliation_required,
                ) = self._run_native_plan(
                    engine=engine,
                    plan=plan,
                    job=job,
                    table_ref=table_ref,
                    routing=native_routing,
                    destination=destination,
                    table_data=table_data,
                    active_intent=active_intent,
                    attempt_id=attempt_id,
                )
                final_status = native_status
                error = native_error
                engine_ref = native_engine_ref or engine_ref
                had_success = native_had_success
                had_skip = native_had_skip
                reconciliation_required = native_reconciliation_required
            else:
                for index, statement in enumerate(statements):
                    existing_budget_error = _effect_budget_error(
                        active_intent,
                        effect_budget_usage,
                    )
                    if existing_budget_error is not None:
                        final_status = JobStatus.PARTIAL
                        error = existing_budget_error
                        break
                    if self._should_stop is not None and self._should_stop():
                        if active_intent is None:
                            return self._store.get_job(job.id)
                        final_status = JobStatus.DEFERRED
                        error = "worker_stopping_before_next_effect"
                        break
                    lock_held = self._renew_table_lock(job)

                    existing_step = None
                    if self._step_ledger is not None:
                        existing_step = self._step_ledger.get_step(job.id, index)
                        if existing_step is not None and existing_step.status in {
                            JobStepStatus.SUCCEEDED,
                            JobStepStatus.SKIPPED,
                        }:
                            # Already-reconciled step -- replaying its recorded outcome, not
                            # submitting new work, so this is safe even without the lock.
                            had_success = had_success or (
                                existing_step.status is JobStepStatus.SUCCEEDED
                            )
                            had_skip = had_skip or (existing_step.status is JobStepStatus.SKIPPED)
                            continue
                        if (
                            existing_step is not None
                            and existing_step.status is JobStepStatus.STARTED
                        ):
                            final_status = JobStatus.RUNNING
                            error = "awaiting_started_step_recovery"
                            break
                    # CR-02: the lock must be held before submitting a *new* statement --
                    # a lost/stolen lock here means another worker may already be mutating
                    # this table under a fresher attempt.
                    if not lock_held:
                        final_status = JobStatus.DEFERRED
                        error = "table_locked"
                        break

                    from cryo.db.session import SessionLocal
                    from cryo.security.abac_enforcement import evaluate_maintenance_allowed

                    try:
                        table_uuid = UUID(job.table_id)
                    except ValueError:
                        table_uuid = None
                    if table_uuid is not None:
                        with SessionLocal() as abac_session:
                            allowed, reason = evaluate_maintenance_allowed(
                                abac_session,
                                table_uuid,
                                None,
                                statement.action,
                            )
                        if not allowed:
                            final_status = JobStatus.FAILED
                            error = reason or "abac_denied"
                            safe_publish(
                                self._run_events,
                                job.run_id,
                                build_log_line(
                                    run_id=job.run_id,
                                    job_id=job.id,
                                    table_id=job.table_id,
                                    table_ref=table_ref,
                                    operation=statement.action.value,
                                    level="error",
                                    message=error,
                                ),
                            )
                            break

                    if not self._preflight_before_step(
                        job=job,
                        destination=destination,
                        table_ref=table_ref,
                        operation=statement.action,
                        detail=statement.sql,
                    ):
                        final_status = JobStatus.RUNNING
                        error = "table_not_quiet"
                        break

                    if self._step_ledger is not None:
                        self._step_ledger.begin_step(
                            job.id,
                            index,
                            statement.action,
                            detail=statement.sql,
                        )

                    receipt_id: str | None = None
                    if active_intent is not None and attempt_id is not None:
                        requirement = next(
                            (
                                item
                                for item in active_intent.capability_requirements
                                if item.operation is statement.action
                                and item.executes_effect
                                and item.engine is EngineType(engine.type)
                                and str(item.destination_id) == job.destination_id
                            ),
                            None,
                        )
                        if requirement is None:
                            raise RuntimeError(
                                f"intent has no effect contract for {statement.action.value}"
                            )
                        receipt = self._intent_store.prepare_effect(
                            intent_id=str(active_intent.id),
                            attempt_id=attempt_id,
                            step_index=index,
                            operation=statement.action,
                            strategy=requirement.effect_strategy,
                            destination_id=job.destination_id,
                        )
                        receipt_id = str(receipt.id)
                        set_context(effect_id=receipt_id)
                        if receipt.state == EffectState.SUCCEEDED.value:
                            had_success = True
                            if self._step_ledger is not None:
                                self._step_ledger.complete_step(
                                    job.id,
                                    index,
                                    JobStepStatus.SUCCEEDED,
                                    engine_ref=receipt.external_reference,
                                    detail="effect receipt already reconciled as succeeded",
                                )
                            continue

                    started = perf_counter()
                    exec_ctx = ExecContext(
                        job_id=job.id,
                        table_id=job.table_id,
                        destination_id=job.destination_id,
                        statement_index=index,
                        replica=job.replica,
                    )

                    def _on_engine_ref(
                        ref: str,
                        *,
                        _job_id: str = job.id,
                        _index: int = index,
                        _receipt_id: str | None = receipt_id,
                    ) -> None:
                        if self._step_ledger is not None:
                            self._step_ledger.set_step_engine_ref(_job_id, _index, ref)
                        set_ref = getattr(self._store, "set_engine_ref", None)
                        if callable(set_ref):
                            set_ref(_job_id, ref)
                        if _receipt_id is not None and self._intent_store is not None:
                            self._intent_store.set_effect_external_reference(_receipt_id, ref)

                    safe_publish(
                        self._run_events,
                        job.run_id,
                        build_log_line(
                            run_id=job.run_id,
                            job_id=job.id,
                            table_id=job.table_id,
                            table_ref=table_ref,
                            operation=statement.action.value,
                            engine=str(engine.type),
                            message=(
                                f"Executing {statement.action.value} "
                                f"({index + 1}/{len(statements)})"
                            ),
                        ),
                    )
                    if receipt_id is not None:
                        self._intent_store.mark_effect_submitted(
                            receipt_id,
                            submission={"operation": statement.action.value},
                        )
                    try:
                        result = _execute_statement(
                            engine,
                            statement,
                            _to_exec_ctx(exec_ctx, on_engine_ref=_on_engine_ref),
                            effect_id=receipt_id,
                            destination_id=job.destination_id,
                        )
                    except Exception as exc:
                        duration_ms = int((perf_counter() - started) * 1000)
                        if receipt_id is not None:
                            self._intent_store.resolve_effect(
                                receipt_id,
                                EffectState.UNKNOWN,
                                error={
                                    "type": type(exc).__name__,
                                    "message": str(exc),
                                },
                            )
                            reconciliation_required = True
                        elif self._step_ledger is not None:
                            self._step_ledger.complete_step(
                                job.id,
                                index,
                                JobStepStatus.FAILED,
                                detail=str(exc),
                            )
                        self._store.record_audit(
                            AuditRecord(
                                job_id=job.id,
                                table_id=job.table_id,
                                action=statement.action,
                                status="UNKNOWN" if receipt_id is not None else "FAILED",
                                engine=engine.type,
                                detail=str(exc),
                                duration_ms=duration_ms,
                                input_files_count=None,
                                output_files_count=None,
                                actor=job.actor,
                            )
                        )
                        safe_publish(
                            self._run_events,
                            job.run_id,
                            build_log_line(
                                run_id=job.run_id,
                                job_id=job.id,
                                table_id=job.table_id,
                                table_ref=table_ref,
                                operation=statement.action.value,
                                engine=str(engine.type),
                                level="error",
                                message=f"{statement.action.value} failed: {exc}",
                            ),
                        )
                        final_status = (
                            JobStatus.PARTIAL if receipt_id is not None else JobStatus.FAILED
                        )
                        error = f"effect_unknown:{exc}" if receipt_id is not None else str(exc)
                        break

                    result_duration_ms = result.duration_ms
                    if result_duration_ms is None:
                        result_duration_ms = int((perf_counter() - started) * 1000)
                    engine_ref = result.engine_ref or engine_ref
                    set_context(engine_ref=engine_ref)
                    if receipt_id is not None and result.engine_ref:
                        self._intent_store.set_effect_external_reference(
                            receipt_id, result.engine_ref
                        )
                    if getattr(result, "skipped", False):
                        had_skip = True
                        detail = result.detail or result.error or "operation skipped"
                        if self._step_ledger is not None:
                            self._step_ledger.complete_step(
                                job.id,
                                index,
                                JobStepStatus.SKIPPED,
                                engine_ref=result.engine_ref,
                                detail=detail,
                            )
                        if receipt_id is not None:
                            self._intent_store.resolve_effect(
                                receipt_id,
                                EffectState.SUCCEEDED,
                                result={"skipped": True, "detail": detail},
                            )
                        self._store.record_audit(
                            AuditRecord(
                                job_id=job.id,
                                table_id=job.table_id,
                                action=statement.action,
                                status="SKIPPED",
                                engine=engine.type,
                                detail=detail,
                                duration_ms=result_duration_ms,
                                actor=job.actor,
                            )
                        )
                        safe_publish(
                            self._run_events,
                            job.run_id,
                            build_log_line(
                                run_id=job.run_id,
                                job_id=job.id,
                                table_id=job.table_id,
                                table_ref=table_ref,
                                operation=statement.action.value,
                                engine=str(engine.type),
                                level="warning",
                                message=f"Skipped {statement.action.value}: {detail}",
                            ),
                        )
                        continue
                    if not getattr(result, "ok", True):
                        detail = result.error or result.detail or "engine execution failed"
                        outcome_unknown = receipt_id is not None and not getattr(
                            result, "outcome_known", True
                        )
                        if self._step_ledger is not None and not outcome_unknown:
                            self._step_ledger.complete_step(
                                job.id,
                                index,
                                JobStepStatus.FAILED,
                                engine_ref=result.engine_ref,
                                detail=detail,
                            )
                        if receipt_id is not None:
                            self._intent_store.resolve_effect(
                                receipt_id,
                                (EffectState.UNKNOWN if outcome_unknown else EffectState.FAILED),
                                error={"message": detail},
                            )
                        if outcome_unknown:
                            reconciliation_required = True
                        self._store.record_audit(
                            AuditRecord(
                                job_id=job.id,
                                table_id=job.table_id,
                                action=statement.action,
                                status="UNKNOWN" if outcome_unknown else "FAILED",
                                engine=engine.type,
                                detail=detail,
                                duration_ms=result_duration_ms,
                                input_files_count=getattr(result, "input_files_count", None),
                                output_files_count=getattr(result, "output_files_count", None),
                                actor=job.actor,
                            )
                        )
                        safe_publish(
                            self._run_events,
                            job.run_id,
                            build_log_line(
                                run_id=job.run_id,
                                job_id=job.id,
                                table_id=job.table_id,
                                table_ref=table_ref,
                                operation=statement.action.value,
                                engine=str(engine.type),
                                level="error",
                                message=f"{statement.action.value} failed: {detail}",
                            ),
                        )
                        final_status = JobStatus.PARTIAL if outcome_unknown else JobStatus.FAILED
                        error = f"effect_unknown:{detail}" if outcome_unknown else detail
                        break
                    had_success = True
                    if self._step_ledger is not None:
                        self._step_ledger.complete_step(
                            job.id,
                            index,
                            JobStepStatus.SUCCEEDED,
                            engine_ref=result.engine_ref,
                            detail=result.detail,
                        )
                    if receipt_id is not None:
                        effect_result = {
                            "engine_ref": result.engine_ref,
                            "detail": result.detail,
                            "duration_ms": result_duration_ms,
                            "input_files_count": getattr(result, "input_files_count", None),
                            "output_files_count": getattr(result, "output_files_count", None),
                            "actual_bytes_freed": getattr(result, "actual_bytes_freed", None),
                            "actual_bytes_written": getattr(result, "actual_bytes_written", None),
                            "objects_deleted": getattr(result, "objects_deleted", None),
                        }
                        self._intent_store.resolve_effect(
                            receipt_id,
                            EffectState.SUCCEEDED,
                            result=effect_result,
                        )
                        effect_budget_usage = _add_effect_budget_usage(
                            effect_budget_usage,
                            result,
                            duration_ms=result_duration_ms,
                        )
                    budget_error = _effect_budget_error(
                        active_intent,
                        effect_budget_usage,
                    )
                    self._store.record_audit(
                        AuditRecord(
                            job_id=job.id,
                            table_id=job.table_id,
                            action=statement.action,
                            status="OK",
                            engine=engine.type,
                            detail=result.detail,
                            duration_ms=result_duration_ms,
                            input_files_count=getattr(result, "input_files_count", None),
                            output_files_count=getattr(result, "output_files_count", None),
                            actual_bytes_freed=getattr(result, "actual_bytes_freed", None),
                            actor=job.actor,
                        )
                    )
                    success_message = (
                        f"Completed {statement.action.value} in {result_duration_ms}ms"
                    )
                    if result.engine_ref:
                        success_message += f" (ref {result.engine_ref})"
                    bytes_freed = getattr(result, "actual_bytes_freed", None)
                    if bytes_freed:
                        success_message += f"; freed {bytes_freed} bytes"
                    safe_publish(
                        self._run_events,
                        job.run_id,
                        build_log_line(
                            run_id=job.run_id,
                            job_id=job.id,
                            table_id=job.table_id,
                            table_ref=table_ref,
                            operation=statement.action.value,
                            engine=str(engine.type),
                            message=success_message,
                        ),
                    )
                    if budget_error is not None:
                        final_status = JobStatus.PARTIAL
                        error = budget_error
                        break
            if had_skip and not had_success and final_status is JobStatus.SUCCEEDED:
                final_status = JobStatus.SKIPPED
            if error in frozenset({"table_not_quiet", "awaiting_started_step_recovery"}):
                if active_intent is None:
                    return self._store.get_job(job.id)
                final_status = JobStatus.DEFERRED
        except MaintenancePausedError as exc:
            final_status = JobStatus.DEFERRED
            error = str(exc)
            safe_publish(
                self._run_events,
                job.run_id,
                build_log_line(
                    run_id=job.run_id,
                    job_id=job.id,
                    table_id=job.table_id,
                    table_ref=table_ref,
                    level="warning",
                    message=f"Job deferred: {exc}",
                ),
            )
        except Exception as exc:
            final_status = JobStatus.FAILED
            error = str(exc)
            safe_publish(
                self._run_events,
                job.run_id,
                build_log_line(
                    run_id=job.run_id,
                    job_id=job.id,
                    table_id=job.table_id,
                    table_ref=table_ref,
                    level="error",
                    message=f"Job failed: {exc}",
                ),
            )

        if attempt_id is not None:
            if reconciliation_required:
                attempt_status = AttemptStatus.RECONCILE_REQUIRED
            elif final_status in {JobStatus.SUCCEEDED, JobStatus.SKIPPED}:
                attempt_status = AttemptStatus.SUCCEEDED
            elif final_status is JobStatus.DEFERRED:
                attempt_status = AttemptStatus.DEFERRED
            else:
                attempt_status = AttemptStatus.FAILED
            self._intent_store.finish_attempt(
                attempt_id,
                attempt_status,
                error={"message": error} if error is not None else None,
            )
        self._store.mark_status(job.id, final_status, engine_ref=engine_ref, error=error)
        if final_status is JobStatus.FAILED and is_table_not_found_error(error):
            self._deactivate_dropped_table(job.table_id, error)
        safe_publish(
            self._run_events,
            job.run_id,
            build_job_status(
                run_id=job.run_id,
                job_id=job.id,
                status=final_status.value,
                table_id=job.table_id,
                table_ref=table_ref,
                message=error,
            ),
        )
        self._queue.complete(job.id, final_status)
        self._release_table_lock(job)
        from cryo.governor.bandit_feedback import finalize_admission

        finalize_admission(
            job.id,
            job_succeeded=final_status in {JobStatus.SUCCEEDED, JobStatus.SKIPPED},
        )
        # Live outage, 2026-09-10: the cost-model retrain used to run inline, here, on
        # every single job completion, on every worker pod -- confirmed live as the real root
        # cause of a fleet-wide worker OOM cascade (kernel-verified via /proc/1/smaps_rollup:
        # Anonymous heap climbed ~1GB per ~6-10s until OOMKilled, always immediately after a
        # job's last operation finished), and independently reproduced by OOMKilling a
        # cryo-api pod via a single call to /api/v1/ai/cost-model/evaluate (same
        # build_outcome_dataset code path). That function's own docstring says "Retrain from
        # audit_log realized savings" -- a batch/background retrain, wired into the hottest,
        # highest-frequency path in the system with zero rate-limiting. Moved to
        # CostModelRefreshRunner (cryo/cli.py), a periodic scheduled daemon matching every
        # other background-refresh job in this codebase (BenefitScoreUpdaterRunner,
        # EvolvingCatalogCleanupRunner) -- see cost_model_refresh_interval_hours (cryo/config.py).
        self._record_job_metrics(
            destination_id=job.destination_id,
            operation_type=_job_operation_type(job),
            status=final_status,
            started_at=job_start,
        )
        if self._on_job_finished is not None:
            self._on_job_finished()
        return self._store.get_job(job.id)

    def _deactivate_dropped_table(self, table_id: str, error: str | None) -> None:
        """Best-effort: never let reconciliation bookkeeping fail the job it's reacting to."""
        try:
            self._store.deactivate_table(
                table_id, f"Table not found in catalog during maintenance: {error}"
            )
        except Exception:  # noqa: BLE001 - best-effort, job outcome already recorded
            pass

    def _record_job_metrics(
        self,
        *,
        destination_id: str,
        operation_type: str,
        status: JobStatus,
        started_at: float,
    ) -> None:
        metric_status = _metric_status(status)
        duration = perf_counter() - started_at
        worker_job_duration_seconds.labels(
            destination=destination_id,
            operation_type=operation_type,
            status=metric_status,
        ).observe(duration)
        worker_job_executions_total.labels(
            destination=destination_id,
            operation_type=operation_type,
            status=metric_status,
        ).inc()

    def _preflight_before_step(
        self,
        *,
        job: MaintenanceJobRecord,
        destination: DestinationConfig,
        table_ref: str | None,
        operation: OperationType,
        detail: str | None,
    ) -> bool:
        """Return False if the table is not quiet enough to begin this step."""
        if self._table_activity_probe is None and not requires_quiet_window(operation):
            return True
        if self._table_activity_probe is None:
            cfg = destination.config or {}
            if not (cfg.get("coordinators") or cfg.get("hosts") or cfg.get("host")):
                engine = destination.engine or ""
                if engine in {
                    EngineType.SPARK_EMR.value,
                    "spark_emr",
                    "spark",
                }:
                    if not cfg.get("application_id") and cfg.get("submit_mode") != "k8s":
                        return True
                if engine in {
                    EngineType.TRINO.value,
                    "trino",
                    EngineType.TRINO,
                }:
                    return True
            from cryo.worker.orphan_recovery import probe_table_activity_for_destination

            def _probe() -> Any:
                return probe_table_activity_for_destination(
                    destination,
                    table_ref,
                    operation=operation,
                    detail=detail,
                    engine_ref=job.engine_ref,
                )

            probe = _probe
        else:
            probe = self._table_activity_probe

        if not requires_quiet_window(operation):
            from cryo.worker.safety_gates import TableActivity

            activity = probe()
            return activity is not TableActivity.ACTIVE
        return wait_for_table_quiet(
            probe,
            operation=operation,
            interval_sec=self._quiet_interval_sec,
            max_wait_sec=self._quiet_max_wait_sec,
            should_stop=self._should_stop,
        )

    def _ensure_table_lock(self, job: MaintenanceJobRecord) -> bool:
        if self._lock_store is None:
            return True
        # The governor normally acquired this lock for this job. An existing lock
        # is sufficient only when the owner matches; another job must block execution.
        return self._lock_store.renew_table_lock(
            job.table_id, job.id, self._lock_ttl_seconds
        ) or self._lock_store.acquire_table_lock(job.table_id, job.id, self._lock_ttl_seconds)

    def _renew_table_lock(self, job: MaintenanceJobRecord) -> bool:
        """Return whether this worker still holds the table lock -- CR-02: every call site
        used to discard this result and kept submitting work after the lock had already
        expired or been taken by another worker. A crash-recovered worker can renew and
        continue past the point another attempt owns the table, corrupting concurrent state
        the lock exists to prevent. Callers MUST stop submitting new work (not just stop
        renewing) the first time this returns False -- see the three loop call sites in this
        file, each of which now breaks/returns instead of proceeding."""
        if self._lock_store is None:
            return True
        return self._lock_store.renew_table_lock(job.table_id, job.id, self._lock_ttl_seconds)

    def _release_table_lock(self, job: MaintenanceJobRecord) -> None:
        if self._lock_store is not None:
            self._lock_store.release_table_lock(job.table_id, job.id)

    def _run_native_plan(
        self,
        *,
        engine: ExecutableMaintenanceEngine,
        plan: MaintenancePlan,
        job: MaintenanceJobRecord,
        table_ref: str | None,
        routing: Any | None = None,
        destination: DestinationConfig | None = None,
        table_data: TableMaintenanceData | None = None,
        active_intent: MaintenanceIntentRecord | None = None,
        attempt_id: str | None = None,
    ) -> tuple[JobStatus, str | None, str | None, bool, bool, bool]:
        from cryo.db.session import SessionLocal
        from cryo.engines.native.routing import (
            NATIVE_DELETE_FILES_DETAIL,
            NativeRoutingResult,
            resolve_native_routing,
        )
        from cryo.security.abac_enforcement import evaluate_maintenance_allowed

        if routing is None:
            stats = table_data.stats if table_data is not None else None
            config = destination.config if destination is not None else {}
            routing = resolve_native_routing(plan, stats, config)
        assert isinstance(routing, NativeRoutingResult)

        had_skip = False
        had_failure = False
        first_error: str | None = None
        for operation, detail in routing.skipped_operations:
            # iceberg-go delete-file rewrite bug: surface as a hard error on OPTIMIZE while
            # still allowing expire_snapshots / remove_orphan_files on the same plan.
            if detail == NATIVE_DELETE_FILES_DETAIL and operation.type is OperationType.OPTIMIZE:
                had_failure = True
                if first_error is None:
                    first_error = detail
                self._store.record_audit(
                    AuditRecord(
                        job_id=job.id,
                        table_id=job.table_id,
                        action=operation.type,
                        status="FAILED",
                        engine=engine.type,
                        detail=detail,
                        duration_ms=0,
                        actor=job.actor,
                    )
                )
                safe_publish(
                    self._run_events,
                    job.run_id,
                    build_log_line(
                        run_id=job.run_id,
                        job_id=job.id,
                        table_id=job.table_id,
                        table_ref=table_ref,
                        operation=operation.type.value,
                        engine=str(engine.type),
                        level="error",
                        message=f"optimize failed: {detail}",
                    ),
                )
                continue
            had_skip = True
            self._record_native_skipped(
                job=job,
                table_ref=table_ref,
                engine=engine,
                operation=operation,
                detail=detail,
            )

        supported_operations = routing.native_operations
        if not supported_operations and not routing.fallback_operations:
            if had_failure and not had_skip:
                return JobStatus.FAILED, first_error, None, False, False, False
            if had_skip:
                return JobStatus.SKIPPED, None, None, False, had_skip, False
            return JobStatus.SKIPPED, None, None, False, False, False

        final_status = JobStatus.SUCCEEDED
        error: str | None = first_error
        engine_ref: str | None = None
        had_success = False
        reconciliation_required = False
        effect_budget_usage = _load_effect_budget_usage(
            self._intent_store,
            active_intent,
        )
        existing_budget_error = _effect_budget_error(
            active_intent,
            effect_budget_usage,
        )
        if existing_budget_error is not None:
            return (
                JobStatus.PARTIAL,
                existing_budget_error,
                None,
                False,
                had_skip,
                False,
            )
        if active_intent is not None and attempt_id is not None and len(supported_operations) > 1:
            # A bundled native subprocess can execute every operation before returning its
            # first result. Durable intents run one operation per submission so an observed
            # budget breach can contain all remaining effects.
            for operation in supported_operations:
                (
                    operation_status,
                    operation_error,
                    operation_engine_ref,
                    operation_had_success,
                    operation_had_skip,
                    operation_reconciliation_required,
                ) = self._run_native_plan(
                    engine=engine,
                    plan=plan,
                    job=job,
                    table_ref=table_ref,
                    routing=NativeRoutingResult(
                        native_operations=(operation,),
                        fallback_operations=(),
                        skipped_operations=(),
                    ),
                    destination=destination,
                    table_data=table_data,
                    active_intent=active_intent,
                    attempt_id=attempt_id,
                )
                had_success = had_success or operation_had_success
                had_skip = had_skip or operation_had_skip
                engine_ref = operation_engine_ref or engine_ref
                set_context(engine_ref=engine_ref)
                if operation_reconciliation_required or operation_status not in {
                    JobStatus.SUCCEEDED,
                    JobStatus.SKIPPED,
                }:
                    if operation_status is JobStatus.FAILED and had_success:
                        operation_status = JobStatus.PARTIAL
                    return (
                        operation_status,
                        operation_error,
                        engine_ref,
                        had_success,
                        had_skip,
                        operation_reconciliation_required,
                    )
            supported_operations = ()
        native_receipts: dict[int, str] = {}
        native_operation_indices: dict[int, int] = {}

        if supported_operations:
            if active_intent is not None and attempt_id is not None:
                pending_operations: list[Operation] = []
                used_indices: set[int] = set()
                for operation in supported_operations:
                    operation_index = next(
                        (
                            index
                            for index, candidate in enumerate(plan.operations)
                            if index not in used_indices and candidate == operation
                        ),
                        None,
                    )
                    if operation_index is None:
                        raise RuntimeError(
                            f"native operation {operation.type.value} is outside bound plan"
                        )
                    used_indices.add(operation_index)
                    requirement = next(
                        (
                            item
                            for item in active_intent.capability_requirements
                            if item.operation is operation.type
                            and item.executes_effect
                            and item.engine is EngineType(engine.type)
                            and str(item.destination_id) == job.destination_id
                        ),
                        None,
                    )
                    if requirement is None:
                        raise RuntimeError(
                            f"intent has no effect contract for {operation.type.value}"
                        )
                    if self._step_ledger is not None:
                        self._step_ledger.begin_step(
                            job.id,
                            operation_index,
                            operation.type,
                            detail="native bundled execution",
                        )
                    receipt = self._intent_store.prepare_effect(
                        intent_id=str(active_intent.id),
                        attempt_id=attempt_id,
                        step_index=operation_index,
                        operation=operation.type,
                        strategy=requirement.effect_strategy,
                        destination_id=job.destination_id,
                    )
                    set_context(effect_id=str(receipt.id))
                    if receipt.state == EffectState.SUCCEEDED.value:
                        had_success = True
                        if self._step_ledger is not None:
                            self._step_ledger.complete_step(
                                job.id,
                                operation_index,
                                JobStepStatus.SUCCEEDED,
                                engine_ref=receipt.external_reference,
                                detail="effect receipt already reconciled as succeeded",
                            )
                        continue
                    native_receipts[operation_index] = str(receipt.id)
                    native_operation_indices[id(operation)] = operation_index
                    pending_operations.append(operation)
                supported_operations = tuple(pending_operations)

        if supported_operations:
            # CR-02: stop before dispatching a new native plan if the lock is already lost --
            # already-reconciled operations above (continue'd via receipt.state == SUCCEEDED)
            # are unaffected since they submit nothing new.
            if not self._renew_table_lock(job):
                return JobStatus.DEFERRED, "table_locked", None, had_success, had_skip, False
            try:
                table_uuid = UUID(job.table_id)
            except ValueError:
                table_uuid = None
            for operation in supported_operations:
                if table_uuid is not None:
                    with SessionLocal() as abac_session:
                        allowed, reason = evaluate_maintenance_allowed(
                            abac_session,
                            table_uuid,
                            None,
                            operation.type,
                        )
                    if not allowed:
                        detail = reason or "abac_denied"
                        safe_publish(
                            self._run_events,
                            job.run_id,
                            build_log_line(
                                run_id=job.run_id,
                                job_id=job.id,
                                table_id=job.table_id,
                                table_ref=table_ref,
                                operation=operation.type.value,
                                level="error",
                                message=detail,
                            ),
                        )
                        return JobStatus.FAILED, detail, None, False, had_skip, False

            native_plan = MaintenancePlan(
                table_ref=plan.table_ref,
                mode=plan.mode,
                operations=supported_operations,
            )
            started = perf_counter()
            chunk_checkpointing = bool(destination.config.get("chunk_checkpointing", False))  # type: ignore[union-attr]
            safe_publish(
                self._run_events,
                job.run_id,
                build_log_line(
                    run_id=job.run_id,
                    job_id=job.id,
                    table_id=job.table_id,
                    table_ref=table_ref,
                    engine=str(engine.type),
                    message=(
                        f"Executing native plan with {len(supported_operations)} operations "
                        f"({'per-chunk checkpointing' if chunk_checkpointing else 'bundled'})"
                    ),
                ),
            )
            if chunk_checkpointing:
                from cryo.db.session import SessionLocal

                session_factory = SessionLocal
            else:
                session_factory = None

            def _on_native_engine_ref(ref: str) -> None:
                set_ref = getattr(self._store, "set_engine_ref", None)
                if callable(set_ref):
                    set_ref(job.id, ref)
                for receipt_id in native_receipts.values():
                    self._intent_store.set_effect_external_reference(receipt_id, ref)

            exec_ctx = _to_exec_ctx(
                ExecContext(
                    job_id=job.id,
                    table_id=job.table_id,
                    destination_id=job.destination_id,
                    statement_index=0,
                    replica=job.replica,
                ),
                run_id=job.run_id,
                on_engine_ref=_on_native_engine_ref,
                on_progress=_native_progress_callback(
                    self._run_events,
                    job=job,
                    table_ref=table_ref,
                    engine=engine.type,
                ),
                session_factory=session_factory,
                chunk_checkpointing=chunk_checkpointing,
            )
            for operation_index, receipt_id in native_receipts.items():
                operation = plan.operations[operation_index]
                self._intent_store.mark_effect_submitted(
                    receipt_id,
                    submission={
                        "operation": operation.type.value,
                        "execution": "native_bundle",
                    },
                )
            try:
                results = _execute_native_plan(
                    engine,
                    native_plan,
                    exec_ctx,
                    effect_count=len(native_receipts),
                    destination_id=job.destination_id,
                )
            except Exception as exc:
                duration_ms = int((perf_counter() - started) * 1000)
                for receipt_id in native_receipts.values():
                    self._intent_store.resolve_effect(
                        receipt_id,
                        EffectState.UNKNOWN,
                        error={"type": type(exc).__name__, "message": str(exc)},
                    )
                fail_action = next(
                    (op.type for op in supported_operations if op.type is OperationType.OPTIMIZE),
                    supported_operations[0].type,
                )
                self._store.record_audit(
                    AuditRecord(
                        job_id=job.id,
                        table_id=job.table_id,
                        action=fail_action,
                        status="UNKNOWN" if native_receipts else "FAILED",
                        engine=engine.type,
                        detail=str(exc),
                        duration_ms=duration_ms,
                        actor=job.actor,
                    )
                )
                safe_publish(
                    self._run_events,
                    job.run_id,
                    build_log_line(
                        run_id=job.run_id,
                        job_id=job.id,
                        table_id=job.table_id,
                        table_ref=table_ref,
                        engine=str(engine.type),
                        level="error",
                        message=f"Native plan failed: {exc}",
                    ),
                )
                return (
                    JobStatus.PARTIAL if native_receipts else JobStatus.FAILED,
                    f"effect_unknown:{exc}" if native_receipts else str(exc),
                    None,
                    False,
                    had_skip,
                    bool(native_receipts),
                )

            resolved_native_receipts: set[str] = set()
            for operation, result in zip(supported_operations, results, strict=False):
                operation_index = native_operation_indices.get(id(operation))
                if operation_index is None:
                    operation_index = next(
                        index
                        for index, candidate in enumerate(plan.operations)
                        if candidate is operation
                    )
                native_receipt_id = native_receipts.get(operation_index)
                result_duration_ms = result.duration_ms
                if result_duration_ms is None:
                    result_duration_ms = int((perf_counter() - started) * 1000)
                engine_ref = result.engine_ref or engine_ref
                set_context(engine_ref=engine_ref)
                if native_receipt_id is not None and result.engine_ref:
                    self._intent_store.set_effect_external_reference(
                        native_receipt_id, result.engine_ref
                    )
                if getattr(result, "skipped", False):
                    had_skip = True
                    detail = result.detail or result.error or "operation skipped"
                    if native_receipt_id is not None:
                        self._intent_store.resolve_effect(
                            native_receipt_id,
                            EffectState.SUCCEEDED,
                            result={"skipped": True, "detail": detail},
                        )
                        resolved_native_receipts.add(native_receipt_id)
                    if self._step_ledger is not None:
                        self._step_ledger.complete_step(
                            job.id,
                            operation_index,
                            JobStepStatus.SKIPPED,
                            engine_ref=result.engine_ref,
                            detail=detail,
                        )
                    self._store.record_audit(
                        AuditRecord(
                            job_id=job.id,
                            table_id=job.table_id,
                            action=operation.type,
                            status="SKIPPED",
                            engine=engine.type,
                            detail=detail,
                            duration_ms=result_duration_ms,
                            actor=job.actor,
                        )
                    )
                    safe_publish(
                        self._run_events,
                        job.run_id,
                        build_log_line(
                            run_id=job.run_id,
                            job_id=job.id,
                            table_id=job.table_id,
                            table_ref=table_ref,
                            operation=operation.type.value,
                            engine=str(engine.type),
                            level="warning",
                            message=f"Skipped {operation.type.value}: {detail}",
                        ),
                    )
                    continue
                if not getattr(result, "ok", True):
                    detail = result.error or result.detail or "engine execution failed"
                    if first_error is None:
                        first_error = detail
                    outcome_unknown = native_receipt_id is not None and not getattr(
                        result, "outcome_known", True
                    )
                    if native_receipt_id is not None:
                        self._intent_store.resolve_effect(
                            native_receipt_id,
                            (EffectState.UNKNOWN if outcome_unknown else EffectState.FAILED),
                            error={"message": detail},
                        )
                        resolved_native_receipts.add(native_receipt_id)
                    if outcome_unknown:
                        reconciliation_required = True
                    else:
                        had_failure = True
                    if self._step_ledger is not None and not outcome_unknown:
                        self._step_ledger.complete_step(
                            job.id,
                            operation_index,
                            JobStepStatus.FAILED,
                            engine_ref=result.engine_ref,
                            detail=detail,
                        )
                    self._store.record_audit(
                        AuditRecord(
                            job_id=job.id,
                            table_id=job.table_id,
                            action=operation.type,
                            status="UNKNOWN" if outcome_unknown else "FAILED",
                            engine=engine.type,
                            detail=detail,
                            duration_ms=result_duration_ms,
                            input_files_count=getattr(result, "input_files_count", None),
                            output_files_count=getattr(result, "output_files_count", None),
                            snapshots_expired=getattr(result, "snapshots_expired", None),
                            manifests_rewritten=getattr(result, "manifests_rewritten", None),
                            actor=job.actor,
                        )
                    )
                    safe_publish(
                        self._run_events,
                        job.run_id,
                        build_log_line(
                            run_id=job.run_id,
                            job_id=job.id,
                            table_id=job.table_id,
                            table_ref=table_ref,
                            operation=operation.type.value,
                            engine=str(engine.type),
                            level="error",
                            message=f"{operation.type.value} failed: {detail}",
                        ),
                    )
                    if not chunk_checkpointing and not outcome_unknown:
                        return (
                            JobStatus.FAILED,
                            detail,
                            engine_ref,
                            had_success,
                            had_skip,
                            False,
                        )
                    continue
                had_success = True
                if native_receipt_id is not None:
                    effect_result = {
                        "engine_ref": result.engine_ref,
                        "detail": result.detail,
                        "duration_ms": result_duration_ms,
                        "input_files_count": getattr(result, "input_files_count", None),
                        "output_files_count": getattr(result, "output_files_count", None),
                        "actual_bytes_freed": getattr(result, "actual_bytes_freed", None),
                        "actual_bytes_written": getattr(result, "actual_bytes_written", None),
                        "objects_deleted": getattr(result, "objects_deleted", None),
                    }
                    self._intent_store.resolve_effect(
                        native_receipt_id,
                        EffectState.SUCCEEDED,
                        result=effect_result,
                    )
                    resolved_native_receipts.add(native_receipt_id)
                    effect_budget_usage = _add_effect_budget_usage(
                        effect_budget_usage,
                        result,
                        duration_ms=result_duration_ms,
                    )
                budget_error = _effect_budget_error(
                    active_intent,
                    effect_budget_usage,
                )
                if self._step_ledger is not None:
                    self._step_ledger.complete_step(
                        job.id,
                        operation_index,
                        JobStepStatus.SUCCEEDED,
                        engine_ref=result.engine_ref,
                        detail=result.detail,
                    )
                self._store.record_audit(
                    AuditRecord(
                        job_id=job.id,
                        table_id=job.table_id,
                        action=operation.type,
                        status="OK",
                        engine=engine.type,
                        detail=result.detail,
                        duration_ms=result_duration_ms,
                        input_files_count=getattr(result, "input_files_count", None),
                        output_files_count=getattr(result, "output_files_count", None),
                        actual_bytes_freed=getattr(result, "actual_bytes_freed", None),
                        snapshots_expired=getattr(result, "snapshots_expired", None),
                        manifests_rewritten=getattr(result, "manifests_rewritten", None),
                        actor=job.actor,
                    )
                )
                success_message = f"Completed {operation.type.value} in {result_duration_ms}ms"
                if result.engine_ref:
                    success_message += f" (ref {result.engine_ref})"
                snapshots_expired = getattr(result, "snapshots_expired", None)
                if snapshots_expired:
                    success_message += f"; expired {snapshots_expired} snapshots"
                manifests_rewritten = getattr(result, "manifests_rewritten", None)
                if manifests_rewritten:
                    success_message += f"; rewrote {manifests_rewritten} manifests"
                bytes_freed = getattr(result, "actual_bytes_freed", None)
                if bytes_freed:
                    success_message += f"; freed {bytes_freed} bytes"
                input_files = getattr(result, "input_files_count", None)
                output_files = getattr(result, "output_files_count", None)
                if input_files is not None and output_files is not None:
                    success_message += f"; {input_files}→{output_files} files"
                safe_publish(
                    self._run_events,
                    job.run_id,
                    build_log_line(
                        run_id=job.run_id,
                        job_id=job.id,
                        table_id=job.table_id,
                        table_ref=table_ref,
                        operation=operation.type.value,
                        engine=str(engine.type),
                        message=success_message,
                    ),
                )
                if budget_error is not None:
                    return (
                        JobStatus.PARTIAL,
                        budget_error,
                        engine_ref,
                        had_success,
                        had_skip,
                        False,
                    )
            unresolved_receipts = set(native_receipts.values()) - resolved_native_receipts
            if unresolved_receipts:
                detail = "native engine returned no result for one or more submitted effects"
                for receipt_id in unresolved_receipts:
                    self._intent_store.resolve_effect(
                        receipt_id,
                        EffectState.UNKNOWN,
                        error={"message": detail},
                    )
                return (
                    JobStatus.PARTIAL,
                    f"effect_unknown:{detail}",
                    engine_ref,
                    had_success,
                    had_skip,
                    True,
                )

            if reconciliation_required:
                return (
                    JobStatus.PARTIAL,
                    f"effect_unknown:{first_error or 'native effect outcome is unknown'}",
                    engine_ref,
                    had_success,
                    had_skip,
                    True,
                )

        if routing.fallback_operations and destination is not None and table_data is not None:
            (
                fallback_status,
                fallback_error,
                fallback_engine_ref,
                fallback_had_success,
                fallback_had_skip,
                fallback_reconciliation_required,
            ) = self._run_sql_fallback_operations(
                operations=routing.fallback_operations,
                plan=plan,
                job=job,
                table_ref=table_ref,
                destination=destination,
                table_data=table_data,
                active_intent=active_intent,
                attempt_id=attempt_id,
            )
            if fallback_had_success:
                had_success = True
            if fallback_had_skip:
                had_skip = True
            if fallback_engine_ref:
                engine_ref = fallback_engine_ref
                set_context(engine_ref=engine_ref)
            if fallback_reconciliation_required:
                return (
                    JobStatus.PARTIAL,
                    fallback_error,
                    engine_ref,
                    had_success,
                    had_skip,
                    True,
                )
            if fallback_status in {JobStatus.FAILED, JobStatus.PARTIAL}:
                return (
                    fallback_status,
                    fallback_error,
                    engine_ref,
                    had_success,
                    had_skip,
                    False,
                )
            if fallback_status is JobStatus.SKIPPED and final_status is JobStatus.SUCCEEDED:
                final_status = JobStatus.SKIPPED

        if had_skip and not had_success and not had_failure:
            final_status = JobStatus.SKIPPED
        elif had_success and had_failure:
            final_status = JobStatus.PARTIAL
        elif had_failure and not had_success:
            final_status = JobStatus.FAILED

        if error is None and first_error is not None:
            error = first_error
        return final_status, error, engine_ref, had_success, had_skip, False

    def _record_native_skipped(
        self,
        *,
        job: MaintenanceJobRecord,
        table_ref: str | None,
        engine: ExecutableMaintenanceEngine,
        operation: Operation,
        detail: str,
    ) -> None:
        self._store.record_audit(
            AuditRecord(
                job_id=job.id,
                table_id=job.table_id,
                action=operation.type,
                status="SKIPPED",
                engine=engine.type,
                detail=detail,
                duration_ms=0,
                actor=job.actor,
            )
        )
        safe_publish(
            self._run_events,
            job.run_id,
            build_log_line(
                run_id=job.run_id,
                job_id=job.id,
                table_id=job.table_id,
                table_ref=table_ref,
                operation=operation.type.value,
                engine=str(engine.type),
                level="warning",
                message=f"Skipped {operation.type.value}: {detail}",
            ),
        )

    def _run_sql_fallback_operations(
        self,
        *,
        operations: Sequence[tuple[Operation, str]],
        plan: MaintenancePlan,
        job: MaintenanceJobRecord,
        table_ref: str | None,
        destination: DestinationConfig,
        table_data: TableMaintenanceData,
        active_intent: MaintenanceIntentRecord | None = None,
        attempt_id: str | None = None,
    ) -> tuple[JobStatus, str | None, str | None, bool, bool, bool]:
        fallback_id = destination.config.get("sql_fallback_destination_id")
        if not isinstance(fallback_id, str) or not fallback_id.strip():
            fallback_id = destination.config.get("fallback_destination_id")
        if not isinstance(fallback_id, str) or not fallback_id.strip():
            return JobStatus.SKIPPED, None, None, False, False, False

        fallback_destination = _try_get_destination(self._store, fallback_id.strip())
        if fallback_destination is None:
            not_found_error = f"sql_fallback_destination_id {fallback_id!r} not found"
            for operation, detail in operations:
                self._record_native_skipped(
                    job=job,
                    table_ref=table_ref,
                    engine=self._engine_factory(destination),
                    operation=operation,
                    detail=f"{detail}; {not_found_error}",
                )
            return JobStatus.FAILED, not_found_error, None, False, True, False

        fallback_engine = self._engine_factory(fallback_destination)
        # Same two hooks the primary dispatch path applies (see _dispatch_job) -- this path
        # never called either, so a fallback Spark destination always got the ambient node
        # IAM role instead of the table's configured S3 keys, and always got the destination's
        # full static executor profile regardless of table size.
        if hasattr(fallback_engine, "with_source_config") and table_data.source_config:
            fallback_engine = fallback_engine.with_source_config(table_data.source_config)
        if hasattr(fallback_engine, "with_table_stats"):
            fallback_engine = fallback_engine.with_table_stats(table_data.stats)
        if fallback_engine.type is EngineType.SPARK_EMR:
            _validate_spark_worker_plan(
                plan,
                table_data=table_data,
                table_ref=table_ref,
            )
        final_status = JobStatus.SUCCEEDED
        error: str | None = None
        engine_ref: str | None = None
        had_success = False
        had_skip = False
        effect_budget_usage = _load_effect_budget_usage(
            self._intent_store,
            active_intent,
        )
        existing_budget_error = _effect_budget_error(
            active_intent,
            effect_budget_usage,
        )
        if existing_budget_error is not None:
            return (
                JobStatus.PARTIAL,
                existing_budget_error,
                None,
                False,
                False,
                False,
            )

        for operation, route_reason in operations:
            lock_held = self._renew_table_lock(job)
            if not native_fallback_supports(fallback_engine.type, operation.type):
                # Recording a skip mutates nothing on the table itself, so this is safe to
                # do even without the lock.
                had_skip = True
                self._record_native_skipped(
                    job=job,
                    table_ref=table_ref,
                    engine=fallback_engine,
                    operation=operation,
                    detail=native_fallback_mismatch_detail(
                        fallback_engine.type,
                        operation.type,
                    ),
                )
                continue
            # CR-02: stop dispatching further fallback operations once the lock is lost --
            # remaining operations are left unattempted so the job can be reconciled/retried
            # under a fresh, verified lock rather than racing another owner.
            if not lock_held:
                final_status = JobStatus.DEFERRED
                error = "table_locked"
                break
            fallback_plan = MaintenancePlan(
                table_ref=plan.table_ref,
                mode=plan.mode,
                operations=(operation,),
            )
            statements = fallback_engine.render(fallback_plan)
            if not statements:
                had_skip = True
                self._record_native_skipped(
                    job=job,
                    table_ref=table_ref,
                    engine=fallback_engine,
                    operation=operation,
                    detail=route_reason,
                )
                continue

            safe_publish(
                self._run_events,
                job.run_id,
                build_log_line(
                    run_id=job.run_id,
                    job_id=job.id,
                    table_id=job.table_id,
                    table_ref=table_ref,
                    operation=operation.type.value,
                    engine=str(fallback_engine.type),
                    level="info",
                    message=(
                        f"Routing {operation.type.value} to {fallback_engine.type.value} "
                        f"({route_reason})"
                    ),
                ),
            )

            operation_index = next(
                index for index, candidate in enumerate(plan.operations) if candidate == operation
            )
            for statement_index, statement in enumerate(statements):
                receipt_id: str | None = None
                effect_index = operation_index * 1000 + statement_index
                if active_intent is not None and attempt_id is not None:
                    requirement = next(
                        (
                            item
                            for item in active_intent.capability_requirements
                            if item.operation is operation.type
                            and item.executes_effect
                            and item.engine is EngineType(fallback_engine.type)
                            and str(item.destination_id) == fallback_destination.id
                        ),
                        None,
                    )
                    if requirement is None:
                        raise RuntimeError(
                            f"intent has no effect contract for {operation.type.value}"
                        )
                    if self._step_ledger is not None:
                        self._step_ledger.begin_step(
                            job.id,
                            effect_index,
                            statement.action,
                            detail=f"SQL fallback: {route_reason}",
                        )
                    receipt = self._intent_store.prepare_effect(
                        intent_id=str(active_intent.id),
                        attempt_id=attempt_id,
                        step_index=effect_index,
                        operation=statement.action,
                        strategy=requirement.effect_strategy,
                        destination_id=fallback_destination.id,
                    )
                    receipt_id = str(receipt.id)
                    set_context(
                        effect_id=receipt_id,
                        destination_id=fallback_destination.id,
                    )
                    if receipt.state == EffectState.SUCCEEDED.value:
                        had_success = True
                        continue
                    self._intent_store.mark_effect_submitted(
                        receipt_id,
                        submission={
                            "operation": statement.action.value,
                            "execution": "sql_fallback",
                            "destination_id": fallback_destination.id,
                        },
                    )
                started = perf_counter()

                def _on_fallback_engine_ref(
                    ref: str,
                    *,
                    _receipt_id: str | None = receipt_id,
                ) -> None:
                    set_ref = getattr(self._store, "set_engine_ref", None)
                    if callable(set_ref):
                        set_ref(job.id, ref)
                    if _receipt_id is not None:
                        self._intent_store.set_effect_external_reference(_receipt_id, ref)

                try:
                    result = _execute_statement(
                        fallback_engine,
                        statement,
                        _to_exec_ctx(
                            ExecContext(
                                job_id=job.id,
                                table_id=job.table_id,
                                destination_id=fallback_destination.id,
                                statement_index=statement_index,
                                replica=job.replica,
                            ),
                            run_id=job.run_id,
                            on_engine_ref=_on_fallback_engine_ref,
                        ),
                        effect_id=receipt_id,
                        destination_id=fallback_destination.id,
                    )
                except Exception as exc:
                    duration_ms = int((perf_counter() - started) * 1000)
                    detail = str(exc)
                    self._store.record_audit(
                        AuditRecord(
                            job_id=job.id,
                            table_id=job.table_id,
                            action=statement.action,
                            status="UNKNOWN" if receipt_id is not None else "FAILED",
                            engine=fallback_engine.type,
                            detail=detail,
                            duration_ms=duration_ms,
                            actor=job.actor,
                        )
                    )
                    if receipt_id is not None:
                        self._intent_store.resolve_effect(
                            receipt_id,
                            EffectState.UNKNOWN,
                            error={"type": type(exc).__name__, "message": detail},
                        )
                    return (
                        JobStatus.PARTIAL if receipt_id is not None else JobStatus.FAILED,
                        f"effect_unknown:{detail}" if receipt_id is not None else detail,
                        None,
                        had_success,
                        had_skip,
                        receipt_id is not None,
                    )

                result_duration_ms = result.duration_ms
                if result_duration_ms is None:
                    result_duration_ms = int((perf_counter() - started) * 1000)
                engine_ref = result.engine_ref or engine_ref
                set_context(engine_ref=engine_ref)
                if receipt_id is not None and result.engine_ref:
                    self._intent_store.set_effect_external_reference(receipt_id, result.engine_ref)

                if getattr(result, "skipped", False):
                    had_skip = True
                    detail = result.detail or result.error or route_reason
                    if receipt_id is not None:
                        self._intent_store.resolve_effect(
                            receipt_id,
                            EffectState.SUCCEEDED,
                            result={"skipped": True, "detail": detail},
                        )
                    if self._step_ledger is not None:
                        self._step_ledger.complete_step(
                            job.id,
                            effect_index,
                            JobStepStatus.SKIPPED,
                            engine_ref=result.engine_ref,
                            detail=detail,
                        )
                    self._store.record_audit(
                        AuditRecord(
                            job_id=job.id,
                            table_id=job.table_id,
                            action=statement.action,
                            status="SKIPPED",
                            engine=fallback_engine.type,
                            detail=detail,
                            duration_ms=result_duration_ms,
                            actor=job.actor,
                        )
                    )
                    continue

                if not getattr(result, "ok", True):
                    detail = result.error or result.detail or "engine execution failed"
                    outcome_unknown = receipt_id is not None and not getattr(
                        result, "outcome_known", True
                    )
                    if receipt_id is not None:
                        self._intent_store.resolve_effect(
                            receipt_id,
                            (EffectState.UNKNOWN if outcome_unknown else EffectState.FAILED),
                            error={"message": detail},
                        )
                    if self._step_ledger is not None and not outcome_unknown:
                        self._step_ledger.complete_step(
                            job.id,
                            effect_index,
                            JobStepStatus.FAILED,
                            engine_ref=result.engine_ref,
                            detail=detail,
                        )
                    self._store.record_audit(
                        AuditRecord(
                            job_id=job.id,
                            table_id=job.table_id,
                            action=statement.action,
                            status="UNKNOWN" if outcome_unknown else "FAILED",
                            engine=fallback_engine.type,
                            detail=detail,
                            duration_ms=result_duration_ms,
                            input_files_count=getattr(result, "input_files_count", None),
                            output_files_count=getattr(result, "output_files_count", None),
                            actor=job.actor,
                        )
                    )
                    return (
                        JobStatus.PARTIAL if outcome_unknown else JobStatus.FAILED,
                        f"effect_unknown:{detail}" if outcome_unknown else detail,
                        engine_ref,
                        had_success,
                        had_skip,
                        outcome_unknown,
                    )

                had_success = True
                if receipt_id is not None:
                    effect_result = {
                        "engine_ref": result.engine_ref,
                        "detail": result.detail,
                        "duration_ms": result_duration_ms,
                        "output_files_count": getattr(result, "output_files_count", None),
                        "actual_bytes_written": getattr(result, "actual_bytes_written", None),
                        "objects_deleted": getattr(result, "objects_deleted", None),
                    }
                    self._intent_store.resolve_effect(
                        receipt_id,
                        EffectState.SUCCEEDED,
                        result=effect_result,
                    )
                    effect_budget_usage = _add_effect_budget_usage(
                        effect_budget_usage,
                        result,
                        duration_ms=result_duration_ms,
                    )
                budget_error = _effect_budget_error(
                    active_intent,
                    effect_budget_usage,
                )
                if self._step_ledger is not None:
                    self._step_ledger.complete_step(
                        job.id,
                        effect_index,
                        JobStepStatus.SUCCEEDED,
                        engine_ref=result.engine_ref,
                        detail=result.detail,
                    )
                self._store.record_audit(
                    AuditRecord(
                        job_id=job.id,
                        table_id=job.table_id,
                        action=statement.action,
                        status="OK",
                        engine=fallback_engine.type,
                        detail=result.detail,
                        duration_ms=result_duration_ms,
                        input_files_count=getattr(result, "input_files_count", None),
                        output_files_count=getattr(result, "output_files_count", None),
                        actual_bytes_freed=getattr(result, "actual_bytes_freed", None),
                        snapshots_expired=getattr(result, "snapshots_expired", None),
                        manifests_rewritten=getattr(result, "manifests_rewritten", None),
                        actor=job.actor,
                    )
                )
                if budget_error is not None:
                    return (
                        JobStatus.PARTIAL,
                        budget_error,
                        engine_ref,
                        had_success,
                        had_skip,
                        False,
                    )
                success_message = (
                    f"Completed {statement.action.value} via {fallback_engine.type.value} "
                    f"in {result_duration_ms}ms"
                )
                safe_publish(
                    self._run_events,
                    job.run_id,
                    build_log_line(
                        run_id=job.run_id,
                        job_id=job.id,
                        table_id=job.table_id,
                        table_ref=table_ref,
                        operation=statement.action.value,
                        engine=str(fallback_engine.type),
                        message=success_message,
                    ),
                )

        if had_skip and not had_success:
            final_status = JobStatus.SKIPPED
        return final_status, error, engine_ref, had_success, had_skip, False


def _validate_spark_worker_plan(
    plan: MaintenancePlan,
    *,
    table_data: TableMaintenanceData,
    table_ref: str | None,
) -> None:
    from cryo.engines.spark.worker_guard import (
        footprint,
        log_spark_payload,
        validate_spark_plan_scope,
    )

    snap = footprint(
        plan=plan,
        stats=table_data.stats,
        partitions=table_data.partitions,
    )
    log_spark_payload(
        stage="worker:pre_execute",
        table_ref=table_ref or plan.table_ref,
        footprint_snap=snap,
    )
    validate_spark_plan_scope(plan, stats=table_data.stats)


def _execute_statement(
    engine: ExecutableMaintenanceEngine,
    statement: RenderedStatement,
    context: ExecCtx,
    *,
    effect_id: str | None,
    destination_id: str,
) -> BaseExecResult:
    with trace_operation(
        "maintenance.effect.execute",
        attributes={
            "cryo.effect.id": effect_id,
            "cryo.operation": statement.action.value,
            "cryo.engine": engine.type.value,
            "cryo.destination.id": destination_id,
        },
    ):
        return engine.execute(statement, context)


def _execute_native_plan(
    engine: ExecutableMaintenanceEngine,
    plan: MaintenancePlan,
    context: ExecCtx,
    *,
    effect_count: int,
    destination_id: str,
) -> list[BaseExecResult]:
    with trace_operation(
        "maintenance.effects.execute_native",
        attributes={
            "cryo.effect.count": effect_count,
            "cryo.operation.count": len(plan.operations),
            "cryo.engine": engine.type.value,
            "cryo.destination.id": destination_id,
        },
    ):
        return engine.execute_plan(plan, context)


def _load_effect_budget_usage(
    intent_store: Any | None,
    intent: MaintenanceIntentRecord | None,
) -> EffectBudgetUsage:
    if intent_store is None or intent is None:
        return EffectBudgetUsage()
    getter = getattr(intent_store, "budget_usage", None)
    if not callable(getter):
        return EffectBudgetUsage()
    usage = getter(str(intent.id))
    if not isinstance(usage, EffectBudgetUsage):
        raise TypeError("intent budget_usage() returned an invalid value")
    return usage


def _add_effect_budget_usage(
    usage: EffectBudgetUsage,
    result: BaseExecResult,
    *,
    duration_ms: int,
) -> EffectBudgetUsage:
    return usage.add(
        output_bytes=getattr(result, "actual_bytes_written", None),
        output_files=getattr(result, "output_files_count", None),
        duration_ms=duration_ms,
        objects_deleted=getattr(result, "objects_deleted", None),
    )


def _effect_budget_error(
    intent: MaintenanceIntentRecord | None,
    usage: EffectBudgetUsage,
) -> str | None:
    if intent is None:
        return None
    try:
        validate_effect_budget(intent, usage)
    except IntentValidationError as exc:
        return f"intent_budget_exceeded:{exc.code}:{exc.detail}"
    return None


def _metric_status(status: JobStatus) -> str:
    if status is JobStatus.SUCCEEDED:
        return "ok"
    if status is JobStatus.SKIPPED:
        return "ok"
    return "error"


def _table_ref_from_stats(stats: Mapping[str, Any]) -> str | None:
    table_ref = stats.get("table_ref") or stats.get("qualified_table_name")
    if isinstance(table_ref, str) and table_ref:
        return table_ref
    schema_name = stats.get("schema_name")
    table_name = stats.get("table_name")
    catalog = stats.get("catalog") or stats.get("catalog_name")
    if catalog and schema_name and table_name:
        return f"{catalog}.{schema_name}.{table_name}"
    if schema_name and table_name:
        return f"{schema_name}.{table_name}"
    return None


def _protected_snapshot_ids(table_id: UUID | str | None) -> list[int]:
    """Return snapshot IDs tagged as protected for a table.

    Reads protected_snapshots via the worker session factory. Returns [] on
    any session failure so a transient DB issue cannot block maintenance.
    """
    if table_id is None:
        return []
    try:
        from cryo.db.repositories.protected_snapshots import ProtectedSnapshotRepository
        from cryo.db.session import SessionLocal
    except Exception:
        return []
    if isinstance(table_id, str):
        try:
            table_uuid = UUID(table_id)
        except (ValueError, TypeError):
            return []
    else:
        table_uuid = table_id
    try:
        with SessionLocal() as session:
            return sorted(ProtectedSnapshotRepository(session).list_snapshot_ids(table_uuid))
    except Exception:
        return []


def _skip_operations_from_preset(
    operations: tuple[OperationType, ...] | None,
) -> list[str]:
    return skip_operations_from_preset(operations)


def _job_operation_type(job: MaintenanceJobRecord) -> str:
    mode = job.mode
    if isinstance(mode, Mode):
        return mode.value
    return str(mode)


def default_engine_factory(destination: DestinationConfig) -> ExecutableMaintenanceEngine:
    from cryo.engines.factory import create_engine

    # destination.config (the raw DB config blob) never carries the destination's own id --
    # engines that need it for K8s Job labeling (native's job_dispatch, Spark-K8s's capacity
    # probe) read config.get("destination_id"), so it must be injected here rather than left
    # for each engine to rediscover. setdefault: an explicit config value (rare) still wins.
    config = dict(destination.config)
    config.setdefault("destination_id", destination.id)
    return create_engine(destination.engine, config)


def _try_get_destination(store: JobStore, destination_id: str) -> DestinationConfig | None:
    getter = getattr(store, "try_get_destination", None)
    if callable(getter):
        return cast(DestinationConfig | None, getter(destination_id))
    try:
        return store.get_destination(destination_id)
    except KeyError:
        return None


def _to_exec_ctx(
    ctx: ExecContext,
    *,
    run_id: str | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    on_engine_ref: Callable[[str], None] | None = None,
    session_factory: Any = None,
    chunk_checkpointing: bool = False,
) -> ExecCtx:
    """Convert worker ExecContext to engine ExecCtx."""
    from uuid import UUID

    def _safe_uuid(value: str | None) -> UUID | None:
        if not value:
            return None
        try:
            return UUID(value)
        except (ValueError, AttributeError):
            return None

    return ExecCtx(
        job_id=_safe_uuid(ctx.job_id),
        table_id=_safe_uuid(ctx.table_id),
        replica=ctx.replica,
        run_id=run_id,
        on_progress=on_progress,
        on_engine_ref=on_engine_ref,
        statement_index=ctx.statement_index,
        session_factory=session_factory,
        chunk_checkpointing=chunk_checkpointing,
    )


def _native_progress_callback(
    run_events: RunEventSink | None,
    *,
    job: MaintenanceJobRecord,
    table_ref: str | None,
    engine: EngineType | str,
) -> Callable[[dict[str, Any]], None] | None:
    if run_events is None:
        return None

    def callback(event: dict[str, Any]) -> None:
        message = _native_progress_message(event)
        safe_publish(
            run_events,
            job.run_id,
            build_log_line(
                run_id=job.run_id,
                job_id=job.id,
                table_id=job.table_id,
                table_ref=table_ref,
                operation=str(event.get("operation") or "native"),
                engine=str(engine),
                message=message,
            ),
        )

    return callback


def _native_progress_message(event: dict[str, Any]) -> str:
    operation = str(event.get("operation") or "native")
    event_name = str(event.get("event") or "progress")
    if event_name == "failed":
        return f"{operation} failed: {event.get('message') or 'unknown error'}"
    pct = event.get("progress_pct")
    files = event.get("files_processed")
    bytes_processed = event.get("bytes_processed")
    if pct is not None:
        return (
            f"{operation}: {event_name} {pct}% ({files or 0} files, {bytes_processed or 0} bytes)"
        )
    if event.get("message"):
        return str(event["message"])
    return f"{operation}: {event_name}"


def main() -> None:
    raise SystemExit("Worker core is library-only in this phase.")


if __name__ == "__main__":
    main()
