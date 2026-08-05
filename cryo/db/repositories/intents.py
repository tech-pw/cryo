from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cryo.capabilities.models import EffectStrategy
from cryo.db.models import (
    AuditLog,
    EffectReceipt,
    MaintenanceAttempt,
    MaintenanceControlState,
    MaintenanceFence,
    MaintenanceIntent,
    MaintenanceJob,
    MaintenanceRun,
)
from cryo.domain.enums import JobStatus, OperationType
from cryo.domain.intents import (
    AttemptStatus,
    EffectBudgetUsage,
    EffectState,
    IntentBinding,
    IntentStatus,
    MaintenanceIntentRecord,
)
from cryo.observability.correlation import correlation_id_from_summary
from cryo.observability.metrics import (
    maintenance_attempt_results_total,
    maintenance_attempts_started_total,
    maintenance_effect_transitions_total,
    maintenance_intent_transitions_total,
    maintenance_pause_blocks_total,
    maintenance_pause_changes_total,
    maintenance_pause_propagation_seconds,
)


class IntentConflictError(RuntimeError):
    pass


class IntentExpiredError(IntentConflictError):
    pass


class StaleFenceError(IntentConflictError):
    pass


class EffectConflictError(IntentConflictError):
    pass


class MaintenancePausedError(IntentConflictError):
    pass


@dataclass(frozen=True, slots=True)
class ReconciliationEffect:
    receipt_id: str
    intent_id: str
    attempt_id: str
    job_id: str
    table_id: str
    destination_id: str
    table_ref: str
    step_index: int
    operation: OperationType
    strategy: EffectStrategy
    state: EffectState
    external_reference: str | None
    submission: dict[str, Any] | None
    probe_count: int
    run_id: str | None = None
    correlation_id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationBacklog:
    count: int
    oldest_created_at: datetime | None


class IntentStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def bind(
        self,
        job_id: str,
        binding: IntentBinding,
        *,
        status: IntentStatus = IntentStatus.READY,
    ) -> MaintenanceIntentRecord:
        with self._session_factory() as session:
            row = bind_intent(
                session,
                job_id=uuid.UUID(job_id),
                binding=binding,
                status=status,
            )
            session.commit()
            session.refresh(row)
            return _intent_record(row)

    def get(self, intent_id: str) -> MaintenanceIntentRecord | None:
        with self._session_factory() as session:
            row = session.get(MaintenanceIntent, uuid.UUID(intent_id))
            return _intent_record(row) if row is not None else None

    def get_for_job(self, job_id: str) -> MaintenanceIntentRecord | None:
        with self._session_factory() as session:
            job = session.get(MaintenanceJob, uuid.UUID(job_id))
            if job is None or job.current_intent_id is None:
                return None
            row = session.get(MaintenanceIntent, job.current_intent_id)
            return _intent_record(row) if row is not None else None

    def current_fence(self, table_id: str) -> int | None:
        with self._session_factory() as session:
            return session.scalar(
                select(MaintenanceFence.epoch).where(
                    MaintenanceFence.table_id == uuid.UUID(table_id)
                )
            )

    def budget_usage(self, intent_id: str) -> EffectBudgetUsage:
        with self._session_factory() as session:
            results = session.scalars(
                select(EffectReceipt.result).where(
                    EffectReceipt.intent_id == uuid.UUID(intent_id),
                    EffectReceipt.state == EffectState.SUCCEEDED.value,
                )
            )
            usage = EffectBudgetUsage()
            for result in results:
                if not isinstance(result, dict):
                    continue
                usage = usage.add(
                    output_bytes=_optional_non_negative_int(result.get("actual_bytes_written")),
                    output_files=_optional_non_negative_int(result.get("output_files_count")),
                    duration_ms=_optional_non_negative_int(result.get("duration_ms")),
                    objects_deleted=_optional_non_negative_int(result.get("objects_deleted")),
                )
            return usage

    def update_status(self, intent_id: str, status: IntentStatus) -> None:
        with self._session_factory() as session:
            row = session.get(MaintenanceIntent, uuid.UUID(intent_id))
            if row is None:
                raise KeyError(f"maintenance intent {intent_id} not found")
            previous = row.status
            row.status = status.value
            session.commit()
            _record_intent_transition(previous, status.value)

    def begin_attempt(
        self,
        intent_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> MaintenanceAttempt:
        with self._session_factory() as session:
            current = now or _database_now(session)
            intent = session.scalar(
                select(MaintenanceIntent)
                .where(MaintenanceIntent.id == uuid.UUID(intent_id))
                .with_for_update()
            )
            if intent is None:
                raise KeyError(f"maintenance intent {intent_id} not found")
            try:
                _assert_intent_executable(session, intent, now=current)
            except IntentExpiredError:
                session.commit()
                raise
            previous_intent_status = intent.status
            active_attempt = session.scalar(
                select(MaintenanceAttempt.id)
                .where(
                    MaintenanceAttempt.intent_id == intent.id,
                    MaintenanceAttempt.status == AttemptStatus.STARTED.value,
                )
                .with_for_update()
            )
            if active_attempt is not None:
                raise IntentConflictError(
                    "maintenance intent already has an active execution attempt"
                )
            attempt_number = (
                session.scalar(
                    select(func.max(MaintenanceAttempt.attempt_number)).where(
                        MaintenanceAttempt.intent_id == intent.id
                    )
                )
                or 0
            ) + 1
            attempt = MaintenanceAttempt(
                intent_id=intent.id,
                attempt_number=attempt_number,
                worker_id=worker_id,
                fencing_epoch=intent.fencing_epoch,
                status=AttemptStatus.STARTED.value,
                started_at=current,
                heartbeat_at=current,
            )
            intent.status = IntentStatus.EXECUTING.value
            session.add(attempt)
            session.commit()
            session.refresh(attempt)
            maintenance_attempts_started_total.inc()
            _record_intent_transition(previous_intent_status, IntentStatus.EXECUTING.value)
            return attempt

    def finish_attempt(
        self,
        attempt_id: str,
        status: AttemptStatus,
        *,
        error: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(UTC)
        with self._session_factory() as session:
            attempt = session.get(MaintenanceAttempt, uuid.UUID(attempt_id))
            if attempt is None:
                raise KeyError(f"maintenance attempt {attempt_id} not found")
            attempt.status = status.value
            attempt.completed_at = now
            attempt.heartbeat_at = now
            attempt.error = error
            intent = session.get(MaintenanceIntent, attempt.intent_id)
            previous_intent_status = intent.status if intent is not None else None
            if intent is not None:
                intent.status = _INTENT_STATUS_BY_ATTEMPT[status].value
            session.commit()
            maintenance_attempt_results_total.labels(status=status.value).inc()
            if intent is not None:
                _record_intent_transition(previous_intent_status, intent.status)

    def prepare_effect(
        self,
        *,
        intent_id: str,
        attempt_id: str,
        step_index: int,
        operation: OperationType,
        strategy: EffectStrategy,
        destination_id: str,
    ) -> EffectReceipt:
        receipt_key = (
            f"effect:{intent_id}:{step_index}:{operation.value}:{destination_id}:{strategy.value}"
        )
        with self._session_factory() as session:
            intent_uuid = uuid.UUID(intent_id)
            attempt_uuid = uuid.UUID(attempt_id)
            intent = session.get(MaintenanceIntent, intent_uuid)
            attempt = session.get(MaintenanceAttempt, attempt_uuid)
            if intent is None:
                raise KeyError(f"maintenance intent {intent_id} not found")
            if attempt is None:
                raise KeyError(f"maintenance attempt {attempt_id} not found")
            _assert_attempt_active(intent, attempt)
            effect_destination_id = uuid.UUID(destination_id)
            _assert_effect_contract(
                intent,
                operation=operation,
                strategy=strategy,
                destination_id=effect_destination_id,
            )
            existing = session.scalar(
                select(EffectReceipt).where(
                    EffectReceipt.intent_id == intent_uuid,
                    EffectReceipt.step_index == step_index,
                )
            )
            if existing is not None:
                if existing.state in {
                    EffectState.SUBMITTED.value,
                    EffectState.UNKNOWN.value,
                }:
                    raise EffectConflictError(
                        f"effect {receipt_key} may already have executed and must be reconciled"
                    )
                if existing.destination_id != effect_destination_id:
                    raise EffectConflictError(
                        f"effect {receipt_key} destination differs from its bound receipt"
                    )
                if (
                    existing.operation_type != operation.value
                    or existing.strategy != strategy.value
                ):
                    raise EffectConflictError(
                        f"effect step {step_index} differs from its bound receipt contract"
                    )
                if existing.state in {
                    EffectState.PREPARED.value,
                    EffectState.FAILED.value,
                }:
                    previous = existing.state
                    existing.attempt_id = attempt_uuid
                    if existing.state == EffectState.FAILED.value:
                        existing.state = EffectState.PREPARED.value
                        existing.external_reference = None
                        existing.submission = None
                        existing.result = None
                        existing.error = None
                        existing.submitted_at = None
                        existing.resolved_at = None
                    session.commit()
                    session.refresh(existing)
                    _record_effect_transition(
                        existing,
                        from_state=previous,
                        to_state=existing.state,
                    )
                return existing
            receipt = EffectReceipt(
                intent_id=intent_uuid,
                attempt_id=attempt_uuid,
                destination_id=effect_destination_id,
                step_index=step_index,
                operation_type=operation.value,
                strategy=strategy.value,
                state=EffectState.PREPARED.value,
                idempotency_key=receipt_key,
            )
            session.add(receipt)
            session.commit()
            session.refresh(receipt)
            _record_effect_transition(
                receipt,
                from_state="none",
                to_state=EffectState.PREPARED.value,
            )
            return receipt

    def mark_effect_submitted(
        self,
        receipt_id: str,
        *,
        external_reference: str | None = None,
        submission: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        with self._session_factory() as session:
            current = now or _database_now(session)
            receipt = session.get(EffectReceipt, uuid.UUID(receipt_id))
            if receipt is None:
                raise KeyError(f"effect receipt {receipt_id} not found")
            if receipt.state != EffectState.PREPARED.value:
                raise EffectConflictError(f"cannot submit effect in state {receipt.state}")
            intent = session.get(MaintenanceIntent, receipt.intent_id)
            if intent is None:
                raise IntentConflictError("effect receipt lost its maintenance intent")
            try:
                _assert_intent_executable(session, intent, now=current)
            except IntentExpiredError:
                session.commit()
                raise
            attempt = session.get(MaintenanceAttempt, receipt.attempt_id)
            if attempt is None:
                raise IntentConflictError("effect receipt lost its maintenance attempt")
            _assert_attempt_active(intent, attempt)
            active_pause = _active_pause_in_session(
                session,
                tenant_id=intent.tenant_id,
                destination_id=receipt.destination_id,
            )
            if active_pause is not None:
                _observe_pause(active_pause, now=current)
                raise MaintenancePausedError(
                    "maintenance execution is paused for the effect destination"
                )
            receipt.state = EffectState.SUBMITTED.value
            receipt.external_reference = external_reference
            receipt.submission = submission
            receipt.submitted_at = current
            session.commit()
            _record_effect_transition(
                receipt,
                from_state=EffectState.PREPARED.value,
                to_state=EffectState.SUBMITTED.value,
            )

    def resolve_effect(
        self,
        receipt_id: str,
        state: EffectState,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        if state not in {
            EffectState.SUCCEEDED,
            EffectState.FAILED,
            EffectState.UNKNOWN,
        }:
            raise ValueError(f"{state.value} is not an effect resolution state")
        now = now or datetime.now(UTC)
        with self._session_factory() as session:
            receipt = session.get(EffectReceipt, uuid.UUID(receipt_id))
            if receipt is None:
                raise KeyError(f"effect receipt {receipt_id} not found")
            if receipt.state not in {
                EffectState.SUBMITTED.value,
                EffectState.UNKNOWN.value,
            }:
                raise EffectConflictError(f"cannot resolve effect in state {receipt.state}")
            previous = receipt.state
            receipt.state = state.value
            receipt.result = result
            receipt.error = error
            if state is not EffectState.UNKNOWN:
                receipt.resolved_at = now
                receipt.next_probe_at = None
            session.commit()
            _record_effect_transition(receipt, from_state=previous, to_state=state.value)

    def set_effect_external_reference(self, receipt_id: str, reference: str) -> None:
        with self._session_factory() as session:
            receipt = session.get(EffectReceipt, uuid.UUID(receipt_id))
            if receipt is None:
                raise KeyError(f"effect receipt {receipt_id} not found")
            if receipt.state not in {
                EffectState.SUBMITTED.value,
                EffectState.UNKNOWN.value,
            }:
                raise EffectConflictError(
                    f"cannot attach an external reference in state {receipt.state}"
                )
            receipt.external_reference = reference
            session.commit()

    def reconciliation_backlog(
        self,
        *,
        now: datetime | None = None,
    ) -> ReconciliationBacklog:
        now = now or datetime.now(UTC)
        with self._session_factory() as session:
            count, oldest = session.execute(
                select(
                    func.count(EffectReceipt.id),
                    func.min(EffectReceipt.created_at),
                ).where(
                    EffectReceipt.state.in_(
                        [EffectState.SUBMITTED.value, EffectState.UNKNOWN.value]
                    ),
                    (EffectReceipt.next_probe_at.is_(None) | (EffectReceipt.next_probe_at <= now)),
                )
            ).one()
            return ReconciliationBacklog(
                count=int(count or 0),
                oldest_created_at=oldest,
            )

    def list_reconciliation_effects(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[ReconciliationEffect]:
        now = now or datetime.now(UTC)
        with self._session_factory() as session:
            rows = session.execute(
                select(EffectReceipt, MaintenanceIntent, MaintenanceJob, MaintenanceRun)
                .join(MaintenanceIntent, MaintenanceIntent.id == EffectReceipt.intent_id)
                .join(MaintenanceJob, MaintenanceJob.id == MaintenanceIntent.job_id)
                .join(MaintenanceRun, MaintenanceRun.id == MaintenanceJob.run_id)
                .where(
                    EffectReceipt.state.in_(
                        [EffectState.SUBMITTED.value, EffectState.UNKNOWN.value]
                    ),
                    (EffectReceipt.next_probe_at.is_(None) | (EffectReceipt.next_probe_at <= now)),
                )
                .order_by(EffectReceipt.created_at)
                .limit(limit)
            )
            return [
                ReconciliationEffect(
                    receipt_id=str(receipt.id),
                    intent_id=str(intent.id),
                    attempt_id=str(receipt.attempt_id),
                    job_id=str(intent.job_id),
                    table_id=str(intent.table_id),
                    destination_id=str(receipt.destination_id),
                    table_ref=intent.table_ref,
                    step_index=receipt.step_index,
                    operation=OperationType(receipt.operation_type),
                    strategy=EffectStrategy(receipt.strategy),
                    state=EffectState(receipt.state),
                    external_reference=receipt.external_reference,
                    submission=receipt.submission,
                    probe_count=receipt.probe_count,
                    run_id=str(job.run_id),
                    correlation_id=correlation_id_from_summary(run.summary),
                    created_at=receipt.created_at,
                )
                for receipt, intent, job, run in rows
            ]

    def defer_effect_probe(
        self,
        receipt_id: str,
        *,
        next_probe_at: datetime,
    ) -> None:
        with self._session_factory() as session:
            receipt = session.get(EffectReceipt, uuid.UUID(receipt_id))
            if receipt is None:
                raise KeyError(f"effect receipt {receipt_id} not found")
            receipt.probe_count += 1
            receipt.next_probe_at = next_probe_at
            session.commit()

    def complete_reconciliation(
        self,
        receipt_id: str,
        *,
        state: EffectState,
        detail: str,
        make_ready: bool = True,
        actor: str = "system:intent-reconciler",
        resolution_source: str = "engine_probe",
        now: datetime | None = None,
    ) -> bool:
        if state not in {EffectState.SUCCEEDED, EffectState.FAILED}:
            raise ValueError("reconciliation must resolve to succeeded or failed")
        now = now or datetime.now(UTC)
        with self._session_factory() as session:
            receipt = session.scalar(
                select(EffectReceipt)
                .where(EffectReceipt.id == uuid.UUID(receipt_id))
                .with_for_update()
            )
            if receipt is None:
                raise KeyError(f"effect receipt {receipt_id} not found")
            if receipt.state not in {
                EffectState.SUBMITTED.value,
                EffectState.UNKNOWN.value,
            }:
                return False
            intent = session.get(MaintenanceIntent, receipt.intent_id)
            attempt = session.get(MaintenanceAttempt, receipt.attempt_id)
            if intent is None or attempt is None:
                raise IntentConflictError("effect receipt lost its intent or attempt")
            job = session.get(MaintenanceJob, intent.job_id)
            fence = session.get(MaintenanceFence, intent.table_id)
            current = (
                job is not None
                and job.current_intent_id == intent.id
                and fence is not None
                and fence.epoch == intent.fencing_epoch
                and fence.owner_intent_id == intent.id
            )
            previous_effect_state = receipt.state
            previous_intent_status = intent.status
            receipt.state = state.value
            receipt.result = {"reconciled": True, "detail": detail}
            receipt.error = None if state is EffectState.SUCCEEDED else {"message": detail}
            receipt.resolved_at = now
            receipt.next_probe_at = None
            attempt.status = (
                AttemptStatus.DEFERRED.value
                if make_ready
                else AttemptStatus.RECONCILE_REQUIRED.value
            )
            attempt.completed_at = attempt.completed_at or now
            attempt.heartbeat_at = now
            intent.status = (
                (IntentStatus.READY.value if make_ready else IntentStatus.RECONCILE_REQUIRED.value)
                if current
                else IntentStatus.SUPERSEDED.value
            )
            if current and make_ready and job is not None:
                job.status = JobStatus.QUEUED.value
                job.error = None
                job.finished_at = None
                job.replica = None
            session.add(
                AuditLog(
                    table_id=intent.table_id,
                    job_id=intent.job_id,
                    action="reconcile_maintenance_effect",
                    status=state.value.upper(),
                    detail=(
                        f"receipt={receipt.id}; source={resolution_source}; "
                        f"continuation={'queued' if current and make_ready else 'held'}; "
                        f"{detail}"
                    ),
                    actor=actor,
                )
            )
            session.commit()
            _record_effect_transition(
                receipt,
                from_state=previous_effect_state,
                to_state=state.value,
            )
            _record_intent_transition(previous_intent_status, intent.status)
            return current

    def is_paused(
        self,
        *,
        tenant_id: str | None = None,
        destination_id: str | None = None,
    ) -> bool:
        scopes = [("global", "global")]
        if tenant_id is not None:
            scopes.append(("tenant", tenant_id))
        if destination_id is not None:
            scopes.append(("destination", destination_id))
        with self._session_factory() as session:
            return (
                session.scalar(
                    select(func.count())
                    .select_from(MaintenanceControlState)
                    .where(
                        MaintenanceControlState.paused.is_(True),
                        tuple_(
                            MaintenanceControlState.scope_type,
                            MaintenanceControlState.scope_id,
                        ).in_(scopes),
                    )
                )
                or 0
            ) > 0

    def set_pause(
        self,
        *,
        scope_type: str,
        scope_id: str,
        paused: bool,
        changed_by: str,
        reason: str | None = None,
    ) -> int:
        with self._session_factory() as session:
            row = set_pause_state(
                session,
                scope_type=scope_type,
                scope_id=scope_id,
                paused=paused,
                changed_by=changed_by,
                reason=reason,
            )
            session.commit()
            maintenance_pause_changes_total.labels(
                scope_type=scope_type,
                action="pause" if paused else "resume",
            ).inc()
            return row.generation


def bind_intent(
    session: Session,
    *,
    job_id: uuid.UUID,
    binding: IntentBinding,
    status: IntentStatus = IntentStatus.READY,
) -> MaintenanceIntent:
    existing = session.scalar(
        select(MaintenanceIntent).where(
            MaintenanceIntent.idempotency_key == binding.idempotency_key
        )
    )
    if existing is not None:
        if existing.job_id != job_id:
            raise IntentConflictError(
                "idempotency key is already bound to a different maintenance job"
            )
        return existing

    job = session.scalar(
        select(MaintenanceJob).where(MaintenanceJob.id == job_id).with_for_update()
    )
    if job is None:
        raise KeyError(f"maintenance job {job_id} not found")
    if job.table_id != binding.table_id or job.destination_id != binding.destination_id:
        raise IntentConflictError("intent scope does not match the maintenance job")

    current = (
        session.get(MaintenanceIntent, job.current_intent_id)
        if job.current_intent_id is not None
        else None
    )
    version = 1 if current is None else current.version + 1
    if current is not None and current.status not in _TERMINAL_INTENT_STATUSES:
        current.status = IntentStatus.SUPERSEDED.value

    intent_id = uuid.uuid4()
    fencing_epoch = _advance_fence(
        session,
        table_id=binding.table_id,
        tenant_id=binding.tenant_id,
        owner_intent_id=intent_id,
    )
    row = MaintenanceIntent(
        id=intent_id,
        tenant_id=binding.tenant_id,
        job_id=job_id,
        parent_intent_id=current.id if current is not None else None,
        version=version,
        status=status.value,
        actor_type=binding.actor_type,
        actor_id=binding.actor_id,
        source_id=binding.source_id,
        table_id=binding.table_id,
        table_ref=binding.table_ref,
        destination_id=binding.destination_id,
        operation_scope=[operation.value for operation in binding.operation_scope],
        observed_snapshot_id=binding.observed_snapshot_id,
        policy_id=binding.policy_id,
        policy_version=binding.policy_version,
        canonical_plan=binding.canonical_plan,
        plan_hash=binding.plan_hash,
        idempotency_key=binding.idempotency_key,
        approval_bindings=[approval.model_dump(mode="json") for approval in binding.approvals],
        fencing_epoch=fencing_epoch,
        budget=binding.budget.model_dump(mode="json"),
        capability_requirements=[
            requirement.model_dump(mode="json") for requirement in binding.capability_requirements
        ],
        containment_action=binding.containment_action.value,
        expires_at=binding.expires_at,
    )
    session.add(row)
    session.flush()
    job.current_intent_id = row.id
    job.intent_version = row.version
    job.plan = row.canonical_plan
    job.plan_hash = row.plan_hash
    return row


def set_pause_state(
    session: Session,
    *,
    scope_type: str,
    scope_id: str,
    paused: bool,
    changed_by: str,
    reason: str | None = None,
) -> MaintenanceControlState:
    if scope_type not in {"global", "tenant", "destination"}:
        raise ValueError(f"unsupported pause scope {scope_type!r}")
    row = session.scalar(
        select(MaintenanceControlState)
        .where(
            MaintenanceControlState.scope_type == scope_type,
            MaintenanceControlState.scope_id == scope_id,
        )
        .with_for_update()
    )
    if row is None:
        row = MaintenanceControlState(
            scope_type=scope_type,
            scope_id=scope_id,
            paused=paused,
            generation=1,
            reason=reason,
            changed_by=changed_by,
        )
        session.add(row)
    else:
        row.paused = paused
        row.generation += 1
        row.reason = reason
        row.changed_by = changed_by
    session.add(
        AuditLog(
            action="maintenance_pause" if paused else "maintenance_resume",
            status="OK",
            detail=(
                f"scope={scope_type}:{scope_id}; generation={row.generation}; "
                f"reason={reason or 'not provided'}"
            ),
            actor=changed_by,
        )
    )
    session.flush()
    return row


def _advance_fence(
    session: Session,
    *,
    table_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    owner_intent_id: uuid.UUID,
) -> int:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        fence = session.get(MaintenanceFence, table_id)
        if fence is None:
            fence = MaintenanceFence(
                table_id=table_id,
                tenant_id=tenant_id,
                epoch=1,
                owner_intent_id=owner_intent_id,
            )
            session.add(fence)
        else:
            fence.epoch += 1
            fence.tenant_id = tenant_id
            fence.owner_intent_id = owner_intent_id
        session.flush()
        return fence.epoch

    statement = (
        insert(MaintenanceFence)
        .values(
            table_id=table_id,
            tenant_id=tenant_id,
            epoch=1,
            owner_intent_id=owner_intent_id,
        )
        .on_conflict_do_update(
            index_elements=[MaintenanceFence.table_id],
            set_={
                "epoch": MaintenanceFence.epoch + 1,
                "tenant_id": tenant_id,
                "owner_intent_id": owner_intent_id,
                "updated_at": func.now(),
            },
        )
        .returning(MaintenanceFence.epoch)
    )
    epoch = session.scalar(statement)
    if epoch is None:
        raise RuntimeError("table fence update returned no epoch")
    return int(epoch)


def _assert_intent_executable(
    session: Session,
    intent: MaintenanceIntent,
    *,
    now: datetime,
) -> None:
    if intent.status not in {IntentStatus.READY.value, IntentStatus.EXECUTING.value}:
        raise IntentConflictError(f"maintenance intent is not executable in state {intent.status}")
    expires_at = (
        intent.expires_at
        if intent.expires_at.tzinfo is not None
        else intent.expires_at.replace(tzinfo=UTC)
    )
    current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    if expires_at <= current:
        intent.status = IntentStatus.EXPIRED.value
        raise IntentExpiredError("maintenance intent has expired")
    _assert_intent_fence(session, intent)
    destination_ids = {intent.destination_id}
    for requirement in intent.capability_requirements:
        if not requirement.get("executes_effect", True):
            continue
        try:
            destination_ids.add(uuid.UUID(str(requirement["destination_id"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise IntentConflictError(
                "maintenance intent has an invalid effect destination"
            ) from exc
    for destination_id in destination_ids:
        active_pause = _active_pause_in_session(
            session,
            tenant_id=intent.tenant_id,
            destination_id=destination_id,
        )
        if active_pause is not None:
            _observe_pause(active_pause, now=now)
            raise MaintenancePausedError("maintenance execution is paused")


def _database_now(session: Session) -> datetime:
    current = session.scalar(select(func.now()))
    if not isinstance(current, datetime):
        raise RuntimeError("database did not return a valid current timestamp")
    return current if current.tzinfo is not None else current.replace(tzinfo=UTC)


def _assert_intent_fence(
    session: Session,
    intent: MaintenanceIntent,
) -> None:
    fence = session.get(MaintenanceFence, intent.table_id)
    if fence is None or fence.epoch != intent.fencing_epoch or fence.owner_intent_id != intent.id:
        raise StaleFenceError("maintenance intent no longer owns the table fence")


def _assert_attempt_active(
    intent: MaintenanceIntent,
    attempt: MaintenanceAttempt,
) -> None:
    if attempt.intent_id != intent.id:
        raise IntentConflictError("maintenance attempt belongs to a different intent")
    if attempt.status != AttemptStatus.STARTED.value:
        raise IntentConflictError(f"maintenance attempt is not active in state {attempt.status}")
    if attempt.fencing_epoch != intent.fencing_epoch:
        raise StaleFenceError("maintenance attempt has a stale fencing epoch")


def _assert_effect_contract(
    intent: MaintenanceIntent,
    *,
    operation: OperationType,
    strategy: EffectStrategy,
    destination_id: uuid.UUID,
) -> None:
    if operation.value not in intent.operation_scope:
        raise EffectConflictError(f"{operation.value} is outside the bound operation scope")
    contracts = [
        item
        for item in intent.capability_requirements
        if item.get("operation") == operation.value
        and item.get("executes_effect", True)
        and item.get("destination_id") == str(destination_id)
    ]
    if not contracts:
        raise EffectConflictError(f"{operation.value} has no bound capability requirement")
    if any(item.get("effect_strategy") != strategy.value for item in contracts):
        raise EffectConflictError(
            f"{operation.value} effect strategy differs from the bound contract"
        )


def _active_pause_in_session(
    session: Session,
    *,
    tenant_id: uuid.UUID | None,
    destination_id: uuid.UUID,
) -> MaintenanceControlState | None:
    conditions = [
        (
            MaintenanceControlState.scope_type == "global",
            MaintenanceControlState.scope_id == "global",
        ),
        (
            MaintenanceControlState.scope_type == "destination",
            MaintenanceControlState.scope_id == str(destination_id),
        ),
    ]
    if tenant_id is not None:
        conditions.append(
            (
                MaintenanceControlState.scope_type == "tenant",
                MaintenanceControlState.scope_id == str(tenant_id),
            )
        )
    for condition in conditions:
        row = session.scalar(
            select(MaintenanceControlState).where(
                MaintenanceControlState.paused.is_(True), *condition
            )
        )
        if row is not None:
            return row
    return None


def _observe_pause(row: MaintenanceControlState, *, now: datetime) -> None:
    maintenance_pause_blocks_total.labels(scope_type=row.scope_type).inc()
    maintenance_pause_propagation_seconds.labels(scope_type=row.scope_type).observe(
        max(0.0, (_as_utc(now) - _as_utc(row.updated_at)).total_seconds())
    )


def _intent_record(row: MaintenanceIntent) -> MaintenanceIntentRecord:
    return MaintenanceIntentRecord.model_validate(
        {
            "id": row.id,
            "job_id": row.job_id,
            "tenant_id": row.tenant_id,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "source_id": row.source_id,
            "table_id": row.table_id,
            "table_ref": row.table_ref,
            "destination_id": row.destination_id,
            "operation_scope": row.operation_scope,
            "observed_snapshot_id": row.observed_snapshot_id,
            "policy_id": row.policy_id,
            "policy_version": row.policy_version,
            "canonical_plan": row.canonical_plan,
            "plan_hash": row.plan_hash,
            "idempotency_key": row.idempotency_key,
            "expires_at": row.expires_at,
            "budget": row.budget,
            "capability_requirements": row.capability_requirements,
            "containment_action": row.containment_action,
            "approvals": row.approval_bindings,
            "version": row.version,
            "parent_intent_id": row.parent_intent_id,
            "status": row.status,
            "fencing_epoch": row.fencing_epoch,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def _record_intent_transition(previous: str | None, current: str) -> None:
    if previous is None or previous == current:
        return
    maintenance_intent_transitions_total.labels(
        from_state=previous,
        to_state=current,
    ).inc()


def _record_effect_transition(
    receipt: EffectReceipt,
    *,
    from_state: str,
    to_state: str,
) -> None:
    if from_state == to_state:
        return
    maintenance_effect_transitions_total.labels(
        operation=receipt.operation_type,
        strategy=receipt.strategy,
        from_state=from_state,
        to_state=to_state,
    ).inc()


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


_TERMINAL_INTENT_STATUSES = {
    IntentStatus.SUCCEEDED.value,
    IntentStatus.FAILED.value,
    IntentStatus.CANCELLED.value,
    IntentStatus.EXPIRED.value,
    IntentStatus.SUPERSEDED.value,
}

_INTENT_STATUS_BY_ATTEMPT = {
    AttemptStatus.SUCCEEDED: IntentStatus.SUCCEEDED,
    AttemptStatus.DEFERRED: IntentStatus.READY,
    AttemptStatus.FAILED: IntentStatus.FAILED,
    AttemptStatus.RECONCILE_REQUIRED: IntentStatus.RECONCILE_REQUIRED,
    AttemptStatus.FENCED: IntentStatus.SUPERSEDED,
    AttemptStatus.STARTED: IntentStatus.EXECUTING,
}
