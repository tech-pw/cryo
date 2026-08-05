from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Protocol

from cryo.capabilities import MaturityTier, SupportStatus, capability_for
from cryo.domain.enums import EngineType
from cryo.domain.intents import (
    EffectBudgetUsage,
    IntentStatus,
    MaintenanceIntentRecord,
)
from cryo.domain.models import MaintenancePlan
from cryo.planner.serialization import hash_plan, plan_from_dict


class IntentJob(Protocol):
    id: str
    table_id: str
    destination_id: str
    plan: dict[str, Any] | None


class IntentValidationError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def validate_execution_intent(
    intent: MaintenanceIntentRecord,
    *,
    job: IntentJob,
    destination_engine: EngineType | str,
    stats: dict[str, Any],
    now: datetime | None = None,
) -> MaintenancePlan:
    now = now or datetime.now(UTC)
    _require(intent.job_id == _uuid_value(job.id), "job_mismatch", "bound job changed")
    _require(
        intent.table_id == _uuid_value(job.table_id),
        "table_mismatch",
        "bound table changed",
    )
    _require(
        intent.destination_id == _uuid_value(job.destination_id),
        "destination_mismatch",
        "bound destination changed",
    )
    _require(
        intent.status is IntentStatus.READY,
        "intent_not_ready",
        f"intent state is {intent.status.value}",
    )
    _require(
        _as_utc(intent.expires_at) > now,
        "intent_expired",
        "execution window elapsed",
    )

    plan = plan_from_dict(intent.canonical_plan)
    actual_hash = hash_plan(plan)
    _require(
        actual_hash == intent.plan_hash,
        "plan_hash_mismatch",
        "canonical plan content does not match its bound hash",
    )
    if job.plan is not None:
        _require(
            hash_plan(plan_from_dict(job.plan)) == intent.plan_hash,
            "job_plan_mismatch",
            "job plan diverged from immutable intent",
        )
    _require(
        tuple(operation.type for operation in plan.operations) == intent.operation_scope,
        "operation_scope_mismatch",
        "plan operations differ from bound operation scope",
    )
    _validate_capabilities(intent, destination_engine=destination_engine)
    _validate_approvals(intent, plan=plan, now=now)
    _validate_snapshot(intent, stats=stats)
    _validate_budget(intent, stats=stats)
    return plan


def validate_effect_budget(
    intent: MaintenanceIntentRecord,
    usage: EffectBudgetUsage,
) -> None:
    budget = intent.budget
    _at_most(
        usage.output_bytes,
        budget.max_output_bytes,
        code="output_bytes_budget_exceeded",
        label="output bytes",
    )
    _at_most(
        usage.output_files,
        budget.max_output_files,
        code="output_files_budget_exceeded",
        label="output files",
    )
    _at_most(
        usage.duration_ms,
        (budget.max_duration_seconds * 1000 if budget.max_duration_seconds is not None else None),
        code="duration_budget_exceeded",
        label="duration milliseconds",
    )
    _at_most(
        usage.objects_deleted,
        budget.max_objects_deleted,
        code="objects_deleted_budget_exceeded",
        label="objects deleted",
    )


def _validate_capabilities(
    intent: MaintenanceIntentRecord,
    *,
    destination_engine: EngineType | str,
) -> None:
    engine = EngineType(destination_engine)
    primary_requirements = [
        requirement
        for requirement in intent.capability_requirements
        if requirement.destination_id == intent.destination_id
    ]
    _require(
        bool(primary_requirements)
        and all(requirement.engine is engine for requirement in primary_requirements),
        "capability_engine_mismatch",
        f"primary destination was not bound for {engine.value}",
    )
    for requirement in intent.capability_requirements:
        capability = capability_for(requirement.engine, requirement.operation)
        _require(
            capability.support not in {SupportStatus.UNSUPPORTED, SupportStatus.CONFIGURATION_ONLY},
            "capability_withdrawn",
            (f"{requirement.engine.value}/{requirement.operation.value} is no longer executable"),
        )
        _require(
            capability.effect_strategy is requirement.effect_strategy,
            "effect_strategy_changed",
            (f"{requirement.engine.value}/{requirement.operation.value} effect contract changed"),
        )
        _require(
            _MATURITY_RANK[capability.maturity] >= _MATURITY_RANK[requirement.minimum_maturity],
            "capability_maturity_regressed",
            (f"{requirement.engine.value}/{requirement.operation.value} maturity regressed"),
        )


def _validate_approvals(
    intent: MaintenanceIntentRecord,
    *,
    plan: MaintenancePlan,
    now: datetime,
) -> None:
    from cryo.workflows.approvals import requires_approval

    approval_by_operation = {approval.operation: approval for approval in intent.approvals}
    for operation in plan.operations:
        if not requires_approval(operation.type):
            continue
        approval = approval_by_operation.get(operation.type)
        _require(
            approval is not None,
            "approval_missing",
            f"{operation.type.value} has no bound approval",
        )
        assert approval is not None
        _require(
            _as_utc(approval.expires_at) > now,
            "approval_expired",
            f"approval for {operation.type.value} expired",
        )
        _require(
            approval.params_hash == _json_hash(operation.params),
            "approval_params_changed",
            f"parameters for {operation.type.value} differ from the approved request",
        )


def _validate_snapshot(
    intent: MaintenanceIntentRecord,
    *,
    stats: dict[str, Any],
) -> None:
    if intent.observed_snapshot_id is None:
        return
    raw = stats.get("raw")
    raw_mapping = raw if isinstance(raw, dict) else {}
    current = stats.get("snapshot_watermark")
    if current is None:
        current = raw_mapping.get("snapshot_watermark")
    if current is None:
        current = raw_mapping.get("current_snapshot_id")
    _require(
        current == intent.observed_snapshot_id,
        "snapshot_changed",
        "table snapshot changed after intent binding; replan is required",
    )


def _validate_budget(
    intent: MaintenanceIntentRecord,
    *,
    stats: dict[str, Any],
) -> None:
    budget = intent.budget
    size_gb = _number(stats.get("total_size_gb"))
    input_bytes = int(size_gb * 1024 * 1024 * 1024) if size_gb is not None else None
    _at_most(
        input_bytes,
        budget.max_input_bytes,
        code="input_bytes_budget_exceeded",
        label="input bytes",
    )
    _at_most(
        _integer(stats.get("total_files")),
        budget.max_input_files,
        code="input_files_budget_exceeded",
        label="input files",
    )
    _at_most(
        _integer(stats.get("total_partitions")),
        budget.max_partitions,
        code="partition_budget_exceeded",
        label="partitions",
    )


def _at_most(
    observed: int | None,
    maximum: int | None,
    *,
    code: str,
    label: str,
) -> None:
    if observed is None or maximum is None:
        return
    _require(observed <= maximum, code, f"{label} {observed} exceeds bound {maximum}")


def _require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise IntentValidationError(code, detail)


def _uuid_value(raw: str) -> Any:
    from uuid import UUID

    try:
        return UUID(raw)
    except (TypeError, ValueError) as exc:
        raise IntentValidationError("invalid_job_scope", f"invalid UUID {raw!r}") from exc


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _json_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


_MATURITY_RANK = {
    MaturityTier.BLOCKED: 0,
    MaturityTier.EXPERIMENTAL: 1,
    MaturityTier.BETA: 2,
    MaturityTier.GA: 3,
}
