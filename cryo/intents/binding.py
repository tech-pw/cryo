from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from cryo.capabilities import SupportStatus, capability_for
from cryo.config import Settings
from cryo.db.models import Destination, MaintenanceIntent, MaintenanceJob, Table
from cryo.db.repositories.intents import bind_intent
from cryo.db.repositories.job_store import (
    _destination_config,
    _job_record,
    _latest_partitions,
    _stats_dict,
)
from cryo.db.repositories.protected_snapshots import ProtectedSnapshotRepository
from cryo.domain.enums import EngineType, OperationType
from cryo.domain.intents import (
    ApprovalBinding,
    CapabilityRequirement,
    ContainmentAction,
    IntentBinding,
    IntentBudget,
    IntentStatus,
)
from cryo.domain.models import MaintenancePlan
from cryo.planner.execution import build_execution_plan
from cryo.planner.serialization import hash_plan, plan_to_dict
from cryo.planner.verifier import PlanVerdict, PlanVerifier
from cryo.workflows.approvals import ApprovalRequest, ApprovalStatus, requires_approval


@dataclass(frozen=True, slots=True)
class BoundJobIntent:
    intent: MaintenanceIntent
    plan: MaintenancePlan
    verdict: PlanVerdict | None


def build_and_bind_job_intent(
    session: Session,
    *,
    job_id: str,
    settings: Settings,
    actor_type: str | None = None,
    actor_id: str | None = None,
    now: datetime | None = None,
) -> BoundJobIntent:
    now = now or datetime.now(UTC)
    job = session.scalar(
        select(MaintenanceJob)
        .where(MaintenanceJob.id == UUID(job_id))
        .options(
            selectinload(MaintenanceJob.run),
            selectinload(MaintenanceJob.table).selectinload(Table.stats),
            selectinload(MaintenanceJob.table).selectinload(Table.source),
        )
    )
    if job is None:
        raise KeyError(f"maintenance job {job_id} not found")
    destination = session.get(Destination, job.destination_id)
    if destination is None:
        raise KeyError(f"destination {job.destination_id} not found")

    job_record = _job_record(session, job)
    # An explicit request bounds the plan. The run summary is where the request is
    # stored, and it is the only place: the job row has no params column. Reading it
    # here, rather than trusting a flag set while the record was built, means the
    # bound plan cannot grow an operation the caller did not ask for (issue #149).
    summary = job.run.summary if job.run is not None else None
    requested = summary.get("operations") if isinstance(summary, dict) else None
    # A job dedupe trimmed (jobs.operations) is narrower than the run's request and already
    # carried into params by _job_record, so the run-level list must not widen it again.
    if isinstance(requested, list) and requested and not job.operations:
        job_record = replace(
            job_record,
            params={**dict(job_record.params), "requested_operations": list(requested)},
        )
    stats = _stats_dict(job.table)
    partitions = _latest_partitions(session, job.table_id)
    protected_snapshots = sorted(
        ProtectedSnapshotRepository(session).list_snapshot_ids(job.table_id)
    )
    plan = build_execution_plan(
        stats=stats,
        partitions=partitions,
        mode=job_record.mode,
        params=dict(job_record.params),
        destination=_destination_config(destination),
        operations=job_record.operations,
        protected_snapshot_ids=protected_snapshots,
        excluded_operations=job_record.excluded_operations,
    )
    return _bind_plan(
        session,
        job=job,
        destination=destination,
        plan=plan,
        stats=stats,
        params=dict(job_record.params),
        policy_operations=job_record.operations,
        settings=settings,
        actor_type=actor_type,
        actor_id=actor_id or job_record.actor,
        now=now,
    )


def bind_explicit_job_intent(
    session: Session,
    *,
    job_id: str,
    plan: MaintenancePlan,
    settings: Settings,
    actor_type: str,
    actor_id: str,
    policy_id: str | None = None,
    now: datetime | None = None,
) -> BoundJobIntent:
    now = now or datetime.now(UTC)
    job = session.scalar(
        select(MaintenanceJob)
        .where(MaintenanceJob.id == UUID(job_id))
        .options(
            selectinload(MaintenanceJob.run),
            selectinload(MaintenanceJob.table).selectinload(Table.stats),
            selectinload(MaintenanceJob.table).selectinload(Table.source),
        )
    )
    if job is None:
        raise KeyError(f"maintenance job {job_id} not found")
    destination = session.get(Destination, job.destination_id)
    if destination is None:
        raise KeyError(f"destination {job.destination_id} not found")
    if policy_id is not None:
        job.run.summary = {**(job.run.summary or {}), "intent_policy_id": policy_id}
    return _bind_plan(
        session,
        job=job,
        destination=destination,
        plan=plan,
        stats=_stats_dict(job.table),
        params={},
        policy_operations=tuple(operation.type for operation in plan.operations),
        settings=settings,
        actor_type=actor_type,
        actor_id=actor_id,
        now=now,
    )


def promote_intents_for_approval(
    session: Session,
    *,
    table_id: Any,
    operation: OperationType,
    approved_by: str,
    settings: Settings,
) -> int:
    pending = list(
        session.scalars(
            select(MaintenanceIntent).where(
                MaintenanceIntent.table_id == table_id,
                MaintenanceIntent.status == IntentStatus.PENDING_APPROVAL.value,
                MaintenanceIntent.operation_scope.contains([operation.value]),
            )
        )
    )
    promoted = 0
    for intent in pending:
        job = session.get(MaintenanceJob, intent.job_id)
        if job is None or job.current_intent_id != intent.id:
            continue
        build_and_bind_job_intent(
            session,
            job_id=str(job.id),
            settings=settings,
            actor_type="approval-promotion",
            actor_id=approved_by,
        )
        promoted += 1
    return promoted


def _bind_plan(
    session: Session,
    *,
    job: MaintenanceJob,
    destination: Destination,
    plan: MaintenancePlan,
    stats: dict[str, Any],
    params: dict[str, Any],
    policy_operations: tuple[OperationType, ...] | None,
    settings: Settings,
    actor_type: str | None,
    actor_id: str | None,
    now: datetime,
) -> BoundJobIntent:
    plan_hash = hash_plan(plan)
    verdict = _verify_plan(session, settings=settings, plan=plan)
    if verdict is not None and not verdict.verified:
        messages = "; ".join(
            violation.message
            for violation in verdict.violations
            if violation.severity == "critical"
        )
        raise ValueError(f"plan verifier rejected durable intent: {messages}")

    approvals, approvals_complete = _approval_bindings(
        session,
        table_id=job.table_id,
        plan=plan,
        now=now,
    )
    requirements = _capability_requirements(
        session,
        destination,
        plan,
        stats=stats,
    )
    canonical_plan = plan_to_dict(plan)
    expires_at = _intent_expiry(job, settings=settings, now=now)
    resolved_actor_type = actor_type or job.run.trigger or job.run.kind
    resolved_actor_id = actor_id or "cryo-system"
    operation_scope = tuple(operation.type for operation in plan.operations)
    observed_snapshot_id = _observed_snapshot_id(stats)
    policy_id = (
        str(job.run.preset_id)
        if job.run.preset_id is not None
        else (job.run.summary or {}).get("intent_policy_id")
    )
    policy_version = _policy_version(params, policy_operations)
    budget = _intent_budget(stats, params, expires_at=expires_at, now=now)
    containment_action = _containment_action(requirements)
    binding = IntentBinding(
        tenant_id=job.table.tenant_id,
        actor_type=resolved_actor_type,
        actor_id=resolved_actor_id,
        source_id=job.table.source_id,
        table_id=job.table_id,
        table_ref=plan.table_ref,
        destination_id=job.destination_id,
        operation_scope=operation_scope,
        observed_snapshot_id=observed_snapshot_id,
        policy_id=policy_id,
        policy_version=policy_version,
        canonical_plan=canonical_plan,
        plan_hash=plan_hash,
        idempotency_key=_idempotency_key(
            job_id=str(job.id),
            semantic_contract={
                "tenant_id": job.table.tenant_id,
                "source_id": job.table.source_id,
                "table_id": job.table_id,
                "table_ref": plan.table_ref,
                "destination_id": job.destination_id,
                "operation_scope": [operation.value for operation in operation_scope],
                "observed_snapshot_id": observed_snapshot_id,
                "policy_id": policy_id,
                "policy_version": policy_version,
                "plan_hash": plan_hash,
                "approvals": [approval.model_dump(mode="json") for approval in approvals],
                "budget": budget.model_dump(mode="json"),
                "capability_requirements": [
                    requirement.model_dump(mode="json") for requirement in requirements
                ],
                "containment_action": containment_action.value,
            },
        ),
        expires_at=expires_at,
        budget=budget,
        capability_requirements=requirements,
        containment_action=containment_action,
        approvals=approvals,
    )
    status = IntentStatus.READY if approvals_complete else IntentStatus.PENDING_APPROVAL
    intent = bind_intent(
        session,
        job_id=job.id,
        binding=binding,
        status=status,
    )
    job.verified = verdict.verified if verdict is not None else False
    job.verifier_version = verdict.verifier_version if verdict is not None else None
    job.verifier_violations = (
        [
            {
                "invariant": violation.invariant,
                "severity": violation.severity,
                "message": violation.message,
                "location": violation.location,
            }
            for violation in verdict.violations
        ]
        if verdict is not None
        else None
    )
    return BoundJobIntent(intent=intent, plan=plan, verdict=verdict)


def _verify_plan(
    session: Session,
    *,
    settings: Settings,
    plan: MaintenancePlan,
) -> PlanVerdict | None:
    if not settings.plan_verifier_enabled:
        return None
    return PlanVerifier(session=session, settings=settings).verify(plan)


def _approval_bindings(
    session: Session,
    *,
    table_id: Any,
    plan: MaintenancePlan,
    now: datetime,
) -> tuple[tuple[ApprovalBinding, ...], bool]:
    bindings: list[ApprovalBinding] = []
    complete = True
    for operation in plan.operations:
        if not requires_approval(operation.type):
            continue
        params_hash = _json_hash(operation.params)
        candidates = session.scalars(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.table_id == table_id,
                ApprovalRequest.operation_type == operation.type.value,
                ApprovalRequest.status == ApprovalStatus.APPROVED.value,
                ApprovalRequest.executed_at.is_(None),
                ApprovalRequest.expires_at.is_not(None),
                ApprovalRequest.expires_at > now,
            )
            .order_by(ApprovalRequest.approved_at.desc())
        )
        match = next(
            (
                candidate
                for candidate in candidates
                if _json_hash(candidate.operation_params) == params_hash
                and candidate.approved_by is not None
                and candidate.approved_at is not None
                and candidate.expires_at is not None
            ),
            None,
        )
        if match is None:
            complete = False
            continue
        assert match.approved_by is not None
        assert match.approved_at is not None
        assert match.expires_at is not None
        bindings.append(
            ApprovalBinding(
                approval_id=match.id,
                approved_by=match.approved_by,
                reason=match.approval_reason,
                approved_at=match.approved_at,
                expires_at=match.expires_at,
                operation=operation.type,
                params_hash=params_hash,
            )
        )
    return tuple(bindings), complete


def _capability_requirements(
    session: Session,
    destination: Destination,
    plan: MaintenancePlan,
    *,
    stats: dict[str, Any],
) -> tuple[CapabilityRequirement, ...]:
    engine = EngineType(destination.engine)
    if engine is not EngineType.NATIVE:
        return tuple(
            _capability_requirement(
                destination,
                operation.type,
                executes_effect=True,
            )
            for operation in plan.operations
        )

    from cryo.engines.native.routing import (
        native_fallback_supports,
        resolve_native_routing,
    )

    routing = resolve_native_routing(plan, stats, destination.config or {})
    native_operation_ids = {id(operation) for operation in routing.native_operations}
    fallback_operation_ids = {id(operation) for operation, _reason in routing.fallback_operations}
    fallback_destination: Destination | None = None
    if fallback_operation_ids:
        raw_fallback_id = (destination.config or {}).get("sql_fallback_destination_id") or (
            destination.config or {}
        ).get("fallback_destination_id")
        try:
            fallback_id = UUID(str(raw_fallback_id))
            fallback_destination = session.get(Destination, fallback_id)
        except (TypeError, ValueError):
            fallback_destination = None
        if fallback_destination is None:
            raise ValueError("native plan requires a configured SQL fallback destination")

    requirements: list[CapabilityRequirement] = []
    for operation in plan.operations:
        if id(operation) in native_operation_ids:
            requirements.append(
                _capability_requirement(
                    destination,
                    operation.type,
                    executes_effect=True,
                )
            )
            continue

        requirements.append(
            _capability_requirement(
                destination,
                operation.type,
                executes_effect=False,
            )
        )
        if id(operation) not in fallback_operation_ids:
            continue
        assert fallback_destination is not None
        fallback_engine = EngineType(fallback_destination.engine)
        if not native_fallback_supports(fallback_engine, operation.type):
            raise ValueError(
                f"{fallback_engine.value} is not a permitted native fallback for "
                f"{operation.type.value}"
            )
        requirements.append(
            _capability_requirement(
                fallback_destination,
                operation.type,
                executes_effect=True,
            )
        )
    return tuple(requirements)


def _capability_requirement(
    destination: Destination,
    operation: OperationType,
    *,
    executes_effect: bool,
) -> CapabilityRequirement:
    engine = EngineType(destination.engine)
    capability = capability_for(engine, operation)
    if capability.support in {
        SupportStatus.UNSUPPORTED,
        SupportStatus.CONFIGURATION_ONLY,
    }:
        raise ValueError(
            f"{engine.value} cannot execute {operation.value}: {capability.support.value}"
        )
    return CapabilityRequirement(
        destination_id=destination.id,
        engine=engine,
        operation=operation,
        minimum_maturity=capability.maturity,
        effect_strategy=capability.effect_strategy,
        executes_effect=executes_effect,
    )


def _intent_expiry(
    job: MaintenanceJob,
    *,
    settings: Settings,
    now: datetime,
) -> datetime:
    scheduled_at = _as_utc(job.run.scheduled_at) if job.run.scheduled_at is not None else None
    base = scheduled_at if scheduled_at is not None and scheduled_at > now else now
    expires_at = base + timedelta(hours=settings.durable_intent_ttl_hours)
    if job.run.deadline_at is not None:
        expires_at = min(expires_at, _as_utc(job.run.deadline_at))
    return expires_at


def _intent_budget(
    stats: dict[str, Any],
    params: dict[str, Any],
    *,
    expires_at: datetime,
    now: datetime,
) -> IntentBudget:
    configured = params.get("intent_budget")
    if isinstance(configured, dict):
        return IntentBudget.model_validate(configured)
    total_size_gb = _non_negative_float(stats.get("total_size_gb"))
    total_files = _non_negative_int(stats.get("total_files"))
    total_partitions = _non_negative_int(stats.get("total_partitions"))
    input_bytes = (
        int(total_size_gb * 1024 * 1024 * 1024 * 1.10) if total_size_gb is not None else None
    )
    file_budget = int(total_files * 1.10) if total_files is not None else None
    return IntentBudget(
        max_input_bytes=input_bytes,
        max_output_bytes=input_bytes,
        max_input_files=file_budget,
        max_output_files=file_budget,
        max_partitions=total_partitions,
        max_duration_seconds=max(1, int((expires_at - now).total_seconds())),
        max_objects_deleted=file_budget,
    )


def _containment_action(
    requirements: tuple[CapabilityRequirement, ...],
) -> ContainmentAction:
    if any(
        requirement.executes_effect and requirement.effect_strategy.value == "manual_reconciliation"
        for requirement in requirements
    ):
        return ContainmentAction.MANUAL_REVIEW
    return ContainmentAction.RECONCILE


def _observed_snapshot_id(stats: dict[str, Any]) -> int | None:
    for value in (
        stats.get("snapshot_watermark"),
        (stats.get("raw") or {}).get("snapshot_watermark"),
        (stats.get("raw") or {}).get("current_snapshot_id"),
    ):
        if isinstance(value, int):
            return value
    return None


def _policy_version(
    params: dict[str, Any],
    operations: tuple[OperationType, ...] | None,
) -> str:
    return _json_hash(
        {
            "params": params,
            "operations": [operation.value for operation in operations or ()],
        }
    )


def _idempotency_key(
    *,
    job_id: str,
    semantic_contract: dict[str, object],
) -> str:
    return f"intent:{job_id}:{_json_hash(semantic_contract)}"


def _json_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return None


def _non_negative_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
