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
        bytes_freed_by_action: dict[OperationType, int] | None = None,
    ) -> None:
        self.type = engine_type
        self.fail_actions = fail_actions or set()
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
    ) -> None:
        self._queue = queue
        self._store = store
        self._engine_factory = engine_factory or default_engine_factory
        self._lock_store = lock_store
        self._on_job_finished = on_job_finished
        self._lock_ttl_seconds = lock_ttl_seconds

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

        final_status = JobStatus.SUCCEEDED
        error: str | None = None
        engine_ref: str | None = None
        try:
            table_data = self._store.get_table_data(job.table_id)
            plan = build_plan(table_data.stats, table_data.partitions, job.mode, job.params)
            self._store.save_plan(job.id, plan_to_dict(plan))

            engine = self._engine_factory(destination)
            statements = engine.render(plan)
            if not statements:
                final_status = JobStatus.SKIPPED
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
                        break
                started = perf_counter()
                exec_ctx = ExecContext(
                    job_id=job.id,
                    table_id=job.table_id,
                    destination_id=job.destination_id,
                    statement_index=index,
                    replica=job.replica,
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
                    final_status = JobStatus.FAILED
                    error = str(exc)
                    break

                result_duration_ms = result.duration_ms
                if result_duration_ms is None:
                    result_duration_ms = int((perf_counter() - started) * 1000)
                engine_ref = result.engine_ref or engine_ref
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
                    final_status = JobStatus.FAILED
                    error = detail
                    break
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
        except Exception as exc:
            final_status = JobStatus.FAILED
            error = str(exc)

        self._store.mark_status(job.id, final_status, engine_ref=engine_ref, error=error)
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


def _metric_status(status: JobStatus) -> str:
    if status is JobStatus.SUCCEEDED:
        return "ok"
    if status is JobStatus.SKIPPED:
        return "ok"
    return "error"


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


def _to_exec_ctx(ctx: ExecContext) -> ExecCtx:
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
    )


def main() -> None:
    raise SystemExit("Worker core is library-only in this phase.")


if __name__ == "__main__":
    main()
