from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol, cast
from uuid import UUID

from cryo.domain.enums import EngineType, JobStatus, JobStepStatus, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation, PartitionPredicate
from cryo.engines.base import ExecCtx, RenderedStatement, is_table_not_found_error
from cryo.engines.base import ExecResult as BaseExecResult
from cryo.observability.metrics import worker_job_duration_seconds, worker_job_executions_total
from cryo.planner.planner import build_plan
from cryo.queue.locks import LockStore
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.queue.run_events import (
    RunEventSink,
    build_job_status,
    build_log_line,
    safe_publish,
)
from cryo.worker.safety_gates import requires_quiet_window, wait_for_table_quiet


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
        if status in {JobStatus.QUEUED, JobStatus.ADMITTED, JobStatus.RUNNING}:
            job.finished_at = None
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

    def list_queued_jobs(self) -> list[MaintenanceJobRecord]:
        return [job for job in self.jobs.values() if job.status is JobStatus.QUEUED]

    def list_deferred_jobs(self) -> list[MaintenanceJobRecord]:
        return [job for job in self.jobs.values() if job.status is JobStatus.DEFERRED]

    def list_running_jobs(self) -> list[MaintenanceJobRecord]:
        return [job for job in self.jobs.values() if job.status is JobStatus.RUNNING]

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
        self._should_stop = should_stop
        self._table_activity_probe = table_activity_probe
        self._quiet_interval_sec = quiet_interval_sec
        self._quiet_max_wait_sec = quiet_max_wait_sec

    def process_next(self) -> MaintenanceJobRecord | None:
        job_id = self._queue.claim_admitted()
        if job_id is None:
            return None

        from cryo.observability.correlation import set_job_id

        set_job_id(job_id)

        job_start = perf_counter()
        job = self._store.get_job(job_id)
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
        self._ensure_table_lock(job)
        self._queue.mark_running(job.id)
        self._store.mark_status(job.id, JobStatus.RUNNING)
        table_ref: str | None = None

        final_status = JobStatus.SUCCEEDED
        error: str | None = None
        engine_ref: str | None = None
        had_success = False
        had_skip = False
        try:
            table_data = self._store.get_table_data(job.table_id)
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
            plan_params = _execution_plan_params(job.params, destination, table_data.stats)
            skip_operations = _skip_operations_from_preset(job.operations)
            if skip_operations:
                plan_params = {**plan_params, "skip_operations": skip_operations}
            if _destination_is_native(destination):
                plan_params = _inject_native_derived_chunk(
                    plan_params,
                    table_data.stats,
                    destination,
                    operations=job.operations,
                )
            plan = build_plan(
                table_data.stats,
                table_data.partitions,
                job.mode,
                plan_params,
                protected_snapshot_ids=_protected_snapshot_ids(job.table_id),
            )
            plan = _gate_native_optimize_manifests(plan, job, destination)
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

            native_routing = None
            if engine.type is EngineType.NATIVE:
                from cryo.engines.native.routing import resolve_native_routing

                native_routing = resolve_native_routing(
                    plan, table_data.stats, destination.config
                )

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
                ) = self._run_native_plan(
                    engine=engine,
                    plan=plan,
                    job=job,
                    table_ref=table_ref,
                    routing=native_routing,
                    destination=destination,
                    table_data=table_data,
                )
                final_status = native_status
                error = native_error
                engine_ref = native_engine_ref or engine_ref
                had_success = native_had_success
                had_skip = native_had_skip
            else:
                for index, statement in enumerate(statements):
                    if self._should_stop is not None and self._should_stop():
                        return self._store.get_job(job.id)
                    self._renew_table_lock(job)

                    existing_step = None
                    if self._step_ledger is not None:
                        existing_step = self._step_ledger.get_step(job.id, index)
                        if existing_step is not None and existing_step.status in {
                            JobStepStatus.SUCCEEDED,
                            JobStepStatus.SKIPPED,
                        }:
                            had_success = had_success or (
                                existing_step.status is JobStepStatus.SUCCEEDED
                            )
                            had_skip = had_skip or (
                                existing_step.status is JobStepStatus.SKIPPED
                            )
                            continue
                        if (
                            existing_step is not None
                            and existing_step.status is JobStepStatus.STARTED
                        ):
                            final_status = JobStatus.RUNNING
                            error = "awaiting_started_step_recovery"
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
                    ) -> None:
                        if self._step_ledger is not None:
                            self._step_ledger.set_step_engine_ref(_job_id, _index, ref)
                        set_ref = getattr(self._store, "set_engine_ref", None)
                        if callable(set_ref):
                            set_ref(_job_id, ref)

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
                    try:
                        result = engine.execute(
                            statement,
                            _to_exec_ctx(exec_ctx, on_engine_ref=_on_engine_ref),
                        )
                    except Exception as exc:
                        duration_ms = int((perf_counter() - started) * 1000)
                        if self._step_ledger is not None:
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
                                status="FAILED",
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
                        final_status = JobStatus.FAILED
                        error = str(exc)
                        break

                    result_duration_ms = result.duration_ms
                    if result_duration_ms is None:
                        result_duration_ms = int((perf_counter() - started) * 1000)
                    engine_ref = result.engine_ref or engine_ref
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
                        if self._step_ledger is not None:
                            self._step_ledger.complete_step(
                                job.id,
                                index,
                                JobStepStatus.FAILED,
                                engine_ref=result.engine_ref,
                                detail=detail,
                            )
                        self._store.record_audit(
                            AuditRecord(
                                job_id=job.id,
                                table_id=job.table_id,
                                action=statement.action,
                                status="FAILED",
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
                        final_status = JobStatus.FAILED
                        error = detail
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
            if had_skip and not had_success and final_status is JobStatus.SUCCEEDED:
                final_status = JobStatus.SKIPPED
            if error in frozenset({"table_not_quiet", "awaiting_started_step_recovery"}):
                return self._store.get_job(job.id)
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
        try:
            from cryo.config import get_settings
            from cryo.db.session import SessionLocal
            from cryo.ml.cost_integration import refresh_cost_model_cache

            with SessionLocal() as session:
                refresh_cost_model_cache(session, get_settings())
        except Exception:
            pass
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
            if not (
                cfg.get("coordinators") or cfg.get("hosts") or cfg.get("host")
            ):
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

    def _ensure_table_lock(self, job: MaintenanceJobRecord) -> None:
        if self._lock_store is None or self._lock_store.is_table_locked(job.table_id):
            return
        self._lock_store.acquire_table_lock(job.table_id, job.id, self._lock_ttl_seconds)

    def _renew_table_lock(self, job: MaintenanceJobRecord) -> None:
        if self._lock_store is None:
            return
        self._lock_store.renew_table_lock(job.table_id, job.id, self._lock_ttl_seconds)

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
    ) -> tuple[JobStatus, str | None, str | None, bool, bool]:
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
                return JobStatus.FAILED, first_error, None, False, False
            if had_skip:
                return JobStatus.SKIPPED, None, None, False, had_skip
            return JobStatus.SKIPPED, None, None, False, False

        final_status = JobStatus.SUCCEEDED
        error: str | None = first_error
        engine_ref: str | None = None
        had_success = False

        if supported_operations:
            self._renew_table_lock(job)
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
                        return JobStatus.FAILED, detail, None, False, had_skip

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
            exec_ctx = _to_exec_ctx(
                ExecContext(
                    job_id=job.id,
                    table_id=job.table_id,
                    destination_id=job.destination_id,
                    statement_index=0,
                    replica=job.replica,
                ),
                run_id=job.run_id,
                on_progress=_native_progress_callback(
                    self._run_events,
                    job=job,
                    table_ref=table_ref,
                    engine=engine.type,
                ),
                session_factory=session_factory,
                chunk_checkpointing=chunk_checkpointing,
            )
            try:
                results = engine.execute_plan(native_plan, exec_ctx)
            except Exception as exc:
                duration_ms = int((perf_counter() - started) * 1000)
                fail_action = next(
                    (
                        op.type
                        for op in supported_operations
                        if op.type is OperationType.OPTIMIZE
                    ),
                    supported_operations[0].type,
                )
                self._store.record_audit(
                    AuditRecord(
                        job_id=job.id,
                        table_id=job.table_id,
                        action=fail_action,
                        status="FAILED",
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
                return JobStatus.FAILED, str(exc), None, False, had_skip

            for operation, result in zip(supported_operations, results, strict=False):
                result_duration_ms = result.duration_ms
                if result_duration_ms is None:
                    result_duration_ms = int((perf_counter() - started) * 1000)
                engine_ref = result.engine_ref or engine_ref
                if getattr(result, "skipped", False):
                    had_skip = True
                    detail = result.detail or result.error or "operation skipped"
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
                    had_failure = True
                    detail = result.error or result.detail or "engine execution failed"
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
                    if not chunk_checkpointing:
                        return JobStatus.FAILED, detail, engine_ref, had_success, had_skip
                    continue
                had_success = True
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

        if routing.fallback_operations and destination is not None and table_data is not None:
            (
                fallback_status,
                fallback_error,
                fallback_engine_ref,
                fallback_had_success,
                fallback_had_skip,
            ) = self._run_sql_fallback_operations(
                operations=routing.fallback_operations,
                plan=plan,
                job=job,
                table_ref=table_ref,
                destination=destination,
                table_data=table_data,
            )
            if fallback_had_success:
                had_success = True
            if fallback_had_skip:
                had_skip = True
            if fallback_engine_ref:
                engine_ref = fallback_engine_ref
            if fallback_status is JobStatus.FAILED:
                return fallback_status, fallback_error, engine_ref, had_success, had_skip
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
        return final_status, error, engine_ref, had_success, had_skip

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
    ) -> tuple[JobStatus, str | None, str | None, bool, bool]:
        fallback_id = destination.config.get("sql_fallback_destination_id")
        if not isinstance(fallback_id, str) or not fallback_id.strip():
            fallback_id = destination.config.get("fallback_destination_id")
        if not isinstance(fallback_id, str) or not fallback_id.strip():
            return JobStatus.SKIPPED, None, None, False, False

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
            return JobStatus.FAILED, not_found_error, None, False, True

        fallback_engine = self._engine_factory(fallback_destination)
        # Same two hooks the primary dispatch path applies (see _dispatch_job) -- this path
        # never called either, so a fallback Spark destination always got the ambient node
        # IAM role instead of the table's configured S3 keys, and always got the destination's
        # full static executor profile regardless of table size.
        if hasattr(fallback_engine, "with_source_config") and table_data.source_config:
            fallback_engine = fallback_engine.with_source_config(table_data.source_config)
        if hasattr(fallback_engine, "with_table_stats"):
            fallback_engine = fallback_engine.with_table_stats(table_data.stats)
        final_status = JobStatus.SUCCEEDED
        error: str | None = None
        engine_ref: str | None = None
        had_success = False
        had_skip = False

        for operation, route_reason in operations:
            self._renew_table_lock(job)
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

            for statement in statements:
                started = perf_counter()
                try:
                    result = fallback_engine.execute(statement)
                except Exception as exc:
                    duration_ms = int((perf_counter() - started) * 1000)
                    detail = str(exc)
                    self._store.record_audit(
                        AuditRecord(
                            job_id=job.id,
                            table_id=job.table_id,
                            action=statement.action,
                            status="FAILED",
                            engine=fallback_engine.type,
                            detail=detail,
                            duration_ms=duration_ms,
                            actor=job.actor,
                        )
                    )
                    return JobStatus.FAILED, detail, None, had_success, had_skip

                result_duration_ms = result.duration_ms
                if result_duration_ms is None:
                    result_duration_ms = int((perf_counter() - started) * 1000)
                engine_ref = result.engine_ref or engine_ref

                if getattr(result, "skipped", False):
                    had_skip = True
                    detail = result.detail or result.error or route_reason
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
                    self._store.record_audit(
                        AuditRecord(
                            job_id=job.id,
                            table_id=job.table_id,
                            action=statement.action,
                            status="FAILED",
                            engine=fallback_engine.type,
                            detail=detail,
                            duration_ms=result_duration_ms,
                            input_files_count=getattr(result, "input_files_count", None),
                            output_files_count=getattr(result, "output_files_count", None),
                            actor=job.actor,
                        )
                    )
                    return JobStatus.FAILED, detail, engine_ref, had_success, had_skip

                had_success = True
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
        return final_status, error, engine_ref, had_success, had_skip


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


def _destination_is_native(destination: DestinationConfig) -> bool:
    engine = destination.engine
    if isinstance(engine, EngineType):
        return engine is EngineType.NATIVE
    return str(engine).lower() in {"native", "enginetype.native"}


def _inject_native_derived_chunk(
    params: Mapping[str, Any],
    stats: Mapping[str, Any],
    destination: DestinationConfig,
    *,
    operations: tuple[OperationType, ...] | None = None,
) -> dict[str, Any]:
    """Size a virtual pod from stats, then set chunk_size_gb to fill that budget."""
    from cryo.engines.native.resources import (
        ResourceCeilings,
        derive_chunk_params,
        estimate_table_resources,
    )

    ceilings = ResourceCeilings.from_config(destination.config)
    base = {k: v for k, v in dict(params).items() if k != "chunk_size_gb"}
    estimate = estimate_table_resources(
        stats,
        operations=operations,
        params=base,
        ceilings=ceilings,
    )
    return derive_chunk_params(estimate.mem_mib, stats, base)


def _execution_plan_params(
    params: Mapping[str, Any],
    destination: DestinationConfig,
    stats: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge destination Trino/Spark catalog into planner params for rendered SQL."""
    from cryo.engines.trino.catalog_bindings import resolve_trino_catalog

    merged = dict(params)
    # Native chunk size is derived at runtime — never trust a leftover preset key.
    if _destination_is_native(destination):
        merged.pop("chunk_size_gb", None)
    engine = destination.engine
    engine_value = engine.value if isinstance(engine, EngineType) else str(engine)
    source_catalog = stats.get("catalog") or stats.get("catalog_name")
    source_catalog_name = source_catalog if isinstance(source_catalog, str) else None
    source_id = stats.get("source_id")

    if engine_value == EngineType.TRINO.value:
        catalog = resolve_trino_catalog(
            destination.config,
            source_id=source_id if isinstance(source_id, (str, UUID)) else None,
            source_catalog_name=source_catalog_name,
        )
    else:
        catalog = destination.config.get("catalog") or destination.config.get("catalog_name")
        if not isinstance(catalog, str) or not catalog:
            catalog = None

    if not isinstance(catalog, str) or not catalog:
        return merged
    merged["catalog"] = catalog
    schema = stats.get("schema_name")
    table = stats.get("table_name")
    if schema and table:
        merged["table_ref"] = f"{catalog}.{schema}.{table}"
    return merged


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
            return sorted(
                ProtectedSnapshotRepository(session).list_snapshot_ids(table_uuid)
            )
    except Exception:
        return []


def _job_operation_type(job: MaintenanceJobRecord) -> str:
    mode = job.mode
    if isinstance(mode, Mode):
        return mode.value
    return str(mode)


def _engine_type_of(engine: EngineType | str) -> EngineType | None:
    if isinstance(engine, EngineType):
        return engine
    try:
        return EngineType(engine)
    except ValueError:
        return None


# The subset of OperationType that a preset's `operations` list can meaningfully gate --
# deliberately excludes conditional sub-operations (rewrite_position_deletes, convert_deletes,
# compute_statistics) that build_plan derives on its own from delete/MOR signals; a preset
# editor listing ["optimize", "optimize_manifests"] wouldn't think to also enumerate those, and
# silently dropping them would break delete-compaction for MOR tables that need it.
_PRESET_GATED_OPERATION_TYPES = (
    OperationType.EXPIRE_SNAPSHOTS,
    OperationType.REMOVE_ORPHAN_FILES,
    OperationType.OPTIMIZE,
    OperationType.OPTIMIZE_MANIFESTS,
)


def _skip_operations_from_preset(operations: tuple[OperationType, ...] | None) -> list[str]:
    """Regression, discovered 2026-07-31 and fixed 2026-08-03: Preset.operations has always
    been editable and looked like it controlled what actually runs, but build_plan() never
    consulted it. An empty/unset list means "no explicit preset configuration" -- don't skip
    anything, matching the pre-existing default behavior for presets that never set this."""
    if not operations:
        return []
    requested = set(operations)
    return [op.value for op in _PRESET_GATED_OPERATION_TYPES if op not in requested]


def _gate_native_optimize_manifests(
    plan: MaintenancePlan,
    job: MaintenanceJobRecord,
    destination: DestinationConfig,
) -> MaintenancePlan:
    """Drop optimize_manifests from a native plan unless the preset configured it.

    The native engine cannot rewrite manifests and optimize_manifests is never routed to
    Trino/Spark. For native destinations we therefore only keep the operation when it is
    explicitly listed in the preset's operations; otherwise it is removed so it never runs.
    """
    if _engine_type_of(destination.engine) is not EngineType.NATIVE:
        return plan
    if all(op.type is not OperationType.OPTIMIZE_MANIFESTS for op in plan.operations):
        return plan
    configured = job.operations
    if configured is not None and OperationType.OPTIMIZE_MANIFESTS in configured:
        return plan
    filtered = tuple(
        op for op in plan.operations if op.type is not OperationType.OPTIMIZE_MANIFESTS
    )
    return MaintenancePlan(
        table_ref=plan.table_ref,
        mode=plan.mode,
        operations=filtered,
        notes=plan.notes,
    )


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


def plan_to_dict(plan: MaintenancePlan) -> dict[str, Any]:
    return {
        "table_ref": plan.table_ref,
        "mode": plan.mode.value,
        "operations": [_operation_to_dict(operation) for operation in plan.operations],
        "notes": list(plan.notes),
    }


def _operation_to_dict(operation: Operation) -> dict[str, Any]:
    return {
        "type": operation.type.value,
        "params": dict(operation.params),
        "predicates": None
        if operation.predicates is None
        else [_predicate_to_dict(predicate) for predicate in operation.predicates],
    }


def _predicate_to_dict(predicate: PartitionPredicate) -> dict[str, Any]:
    return {
        "unsupported_reason": predicate.unsupported_reason,
        "terms": [
            {
                "base_column": term.base_column,
                "transform": term.transform.value,
                "value": term.value,
                "col_type": term.col_type,
                "transform_param": term.transform_param,
                "partition_field": term.partition_field,
                "unsupported_reason": term.unsupported_reason,
            }
            for term in predicate.terms
        ],
    }


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
            f"{operation}: {event_name} {pct}%"
            f" ({files or 0} files, {bytes_processed or 0} bytes)"
        )
    if event.get("message"):
        return str(event["message"])
    return f"{operation}: {event_name}"


def main() -> None:
    raise SystemExit("Worker core is library-only in this phase.")


if __name__ == "__main__":
    main()
