from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol, cast
from uuid import UUID

from cryo.domain.enums import EngineType, JobStatus, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation, PartitionPredicate
from cryo.engines.base import ExecCtx, RenderedStatement
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
    type: EngineType

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]: ...

    def session_setup(self) -> list[str]: ...

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> BaseExecResult: ...


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


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, MaintenanceJobRecord] = {}
        self.destinations: dict[str, DestinationConfig] = {}
        self.tables: dict[str, TableMaintenanceData] = {}
        self.audit: list[AuditRecord] = []

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
    ) -> None:
        self._queue = queue
        self._store = store
        self._engine_factory = engine_factory or default_engine_factory
        self._lock_store = lock_store
        self._on_job_finished = on_job_finished
        self._lock_ttl_seconds = lock_ttl_seconds
        self._run_events = run_events

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
            plan = build_plan(
                table_data.stats,
                table_data.partitions,
                job.mode,
                _execution_plan_params(job.params, destination, table_data.stats),
            )
            self._store.save_plan(job.id, plan_to_dict(plan))

            engine = self._engine_factory(destination)
            if hasattr(engine, "with_source_config") and table_data.source_config:
                engine = engine.with_source_config(table_data.source_config)
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
            if not statements:
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
                )
                final_status = native_status
                error = native_error
                engine_ref = native_engine_ref or engine_ref
                had_success = native_had_success
                had_skip = native_had_skip
            else:
                for index, statement in enumerate(statements):
                    self._renew_table_lock(job)
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
                    started = perf_counter()
                    exec_ctx = ExecContext(
                        job_id=job.id,
                        table_id=job.table_id,
                        destination_id=job.destination_id,
                        statement_index=index,
                        replica=job.replica,
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
                            message=(
                                f"Executing {statement.action.value} "
                                f"({index + 1}/{len(statements)})"
                            ),
                        ),
                    )
                    try:
                        result = engine.execute(statement, _to_exec_ctx(exec_ctx))
                    except Exception as exc:
                        duration_ms = int((perf_counter() - started) * 1000)
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
    ) -> tuple[JobStatus, str | None, str | None, bool, bool]:
        from cryo.db.session import SessionLocal
        from cryo.security.abac_enforcement import evaluate_maintenance_allowed

        supported_operations = tuple(
            operation
            for operation in plan.operations
            if operation.type
            in {
                OperationType.EXPIRE_SNAPSHOTS,
                OperationType.REMOVE_ORPHAN_FILES,
                OperationType.OPTIMIZE,
                OperationType.OPTIMIZE_MANIFESTS,
            }
        )
        if not supported_operations:
            return JobStatus.SKIPPED, None, None, False, False

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
                    return JobStatus.FAILED, detail, None, False, False

        native_plan = MaintenancePlan(
            table_ref=plan.table_ref,
            mode=plan.mode,
            operations=supported_operations,
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
                message=(
                    f"Executing native plan with {len(supported_operations)} operations "
                    "in one subprocess"
                ),
            ),
        )
        started = perf_counter()
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
        )
        try:
            results = engine.execute_plan(native_plan, exec_ctx)
        except Exception as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            self._store.record_audit(
                AuditRecord(
                    job_id=job.id,
                    table_id=job.table_id,
                    action=supported_operations[0].type,
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
            return JobStatus.FAILED, str(exc), None, False, False

        final_status = JobStatus.SUCCEEDED
        error: str | None = None
        engine_ref: str | None = None
        had_success = False
        had_skip = False
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
                detail = result.error or result.detail or "engine execution failed"
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
                return JobStatus.FAILED, detail, engine_ref, had_success, had_skip
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


def _execution_plan_params(
    params: Mapping[str, Any],
    destination: DestinationConfig,
    stats: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge destination Trino/Spark catalog into planner params for rendered SQL."""
    merged = dict(params)
    catalog = destination.config.get("catalog") or destination.config.get("catalog_name")
    if not isinstance(catalog, str) or not catalog:
        return merged
    merged["catalog"] = catalog
    schema = stats.get("schema_name")
    table = stats.get("table_name")
    if schema and table:
        merged["table_ref"] = f"{catalog}.{schema}.{table}"
    return merged


def _job_operation_type(job: MaintenanceJobRecord) -> str:
    mode = job.mode
    if isinstance(mode, Mode):
        return mode.value
    return str(mode)


def default_engine_factory(destination: DestinationConfig) -> ExecutableMaintenanceEngine:
    from cryo.engines.factory import create_engine

    return create_engine(destination.engine, destination.config)


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
