from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import (
    AuditLog,
    Destination,
    LayoutRecommendation,
    MaintenanceControlState,
    Table,
)
from cryo.rollout.safety import (
    RolloutDecision,
    RolloutPolicy,
    evaluate_rollout,
    load_rollout_observation,
)

MAX_SAFETY_EVIDENCE_AGE = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class ProductionSafetyReport:
    status: str
    destination_id: uuid.UUID
    policy_sha256: str | None
    attempts: int
    failed_attempts: int
    effects: int
    unknown_effects: int
    failure_rate: float | None
    unknown_effect_rate: float | None
    rollback_drill_at: str | None
    current_destination_paused: bool
    qualifies_for_canary: bool
    reasons: tuple[str, ...]


def evaluate_production_safety(
    session: Session,
    destination_id: uuid.UUID,
    *,
    now: datetime | None = None,
    drill_max_age_days: int = 30,
) -> ProductionSafetyReport:
    destination = session.get(Destination, destination_id)
    if destination is None:
        raise ValueError(f"Destination not found: {destination_id}")
    current = now or datetime.now(UTC)
    reasons: list[str] = []
    if not destination.enabled:
        reasons.append("destination_disabled")
    raw_policy = (destination.config or {}).get("rollout_policy")
    if not isinstance(raw_policy, dict):
        return _report(destination_id, reasons=["missing_rollout_policy"])
    try:
        policy = RolloutPolicy.model_validate(raw_policy)
    except ValidationError:
        return _report(destination_id, reasons=["invalid_rollout_policy"])
    policy_payload = json.dumps(raw_policy, sort_keys=True, separators=(",", ":"))
    policy_sha256 = hashlib.sha256(policy_payload.encode()).hexdigest()
    if not policy.enabled:
        reasons.append("rollout_policy_disabled")
    if policy.phase != "canary":
        reasons.append("rollout_policy_not_canary")
    observation = load_rollout_observation(
        session,
        destination_id=destination_id,
        cutoff=current - timedelta(minutes=policy.window_minutes),
    )
    evaluation = evaluate_rollout(policy, observation)
    if evaluation.decision is not RolloutDecision.HEALTHY:
        reasons.append(f"rollout_{evaluation.decision.value}")
    if observation.effects < policy.min_attempts:
        reasons.append("insufficient_effect_receipts")
    control = session.scalar(
        select(MaintenanceControlState).where(
            MaintenanceControlState.scope_type == "destination",
            MaintenanceControlState.scope_id == str(destination_id),
        )
    )
    paused = control.paused if control is not None else False
    if paused:
        reasons.append("destination_paused")
    drill_at = _latest_completed_drill(
        session,
        destination_id,
        cutoff=current - timedelta(days=drill_max_age_days),
    )
    if drill_at is None:
        reasons.append("missing_recent_automatic_halt_resume_drill")
    return ProductionSafetyReport(
        status="qualified" if not reasons else "blocked",
        destination_id=destination_id,
        policy_sha256=policy_sha256,
        attempts=observation.attempts,
        failed_attempts=observation.failed_attempts,
        effects=observation.effects,
        unknown_effects=observation.unknown_effects,
        failure_rate=round(evaluation.failure_rate, 6),
        unknown_effect_rate=round(evaluation.unknown_effect_rate, 6),
        rollback_drill_at=drill_at.isoformat() if drill_at is not None else None,
        current_destination_paused=paused,
        qualifies_for_canary=not reasons,
        reasons=tuple(sorted(set(reasons))),
    )


def record_production_safety(
    session: Session,
    table_id: uuid.UUID,
    destination_id: uuid.UUID,
    *,
    actor: str,
) -> ProductionSafetyReport:
    if session.get(Table, table_id) is None:
        raise ValueError(f"Table not found: {table_id}")
    recommendation = session.scalar(
        select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
    )
    if recommendation is None or (recommendation.detail or {}).get("source") != "pto":
        raise ValueError(f"PTO recommendation not found for table: {table_id}")
    report = evaluate_production_safety(session, destination_id)
    evidence: dict[str, object] = {
        "schema": "production-safety/v1",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "report": _report_payload(report),
    }
    detail = dict(recommendation.detail or {})
    detail["production_safety"] = evidence
    recommendation.detail = detail
    session.add(
        AuditLog(
            table_id=table_id,
            action="pto_production_safety_recorded",
            status="QUALIFIED" if report.qualifies_for_canary else "BLOCKED",
            engine="rollout_safety",
            detail=json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            actor=actor,
        )
    )
    session.commit()
    return report


def is_production_safety_evidence_current(
    session: Session,
    evidence: Mapping[object, object],
    *,
    now: datetime | None = None,
) -> bool:
    evaluated_at = evidence.get("evaluated_at")
    report = evidence.get("report")
    if not isinstance(evaluated_at, str) or not isinstance(report, Mapping):
        return False
    try:
        evaluated = datetime.fromisoformat(evaluated_at)
    except ValueError:
        return False
    if evaluated.tzinfo is None:
        evaluated = evaluated.replace(tzinfo=UTC)
    if (now or datetime.now(UTC)) - evaluated > MAX_SAFETY_EVIDENCE_AGE:
        return False
    destination_id = report.get("destination_id")
    policy_sha256 = report.get("policy_sha256")
    if not isinstance(destination_id, str) or not isinstance(policy_sha256, str):
        return False
    try:
        destination = session.get(Destination, uuid.UUID(destination_id))
    except ValueError:
        return False
    if destination is None:
        return False
    raw_policy = (destination.config or {}).get("rollout_policy")
    return _payload_sha256(raw_policy) == policy_sha256


def _latest_completed_drill(
    session: Session,
    destination_id: uuid.UUID,
    *,
    cutoff: datetime,
) -> datetime | None:
    scope_token = f"scope=destination:{destination_id};"
    rows = list(
        session.scalars(
            select(AuditLog)
            .where(
                AuditLog.ts >= cutoff,
                AuditLog.action.in_(("maintenance_pause", "maintenance_resume")),
                AuditLog.detail.contains(scope_token),
            )
            .order_by(AuditLog.ts, AuditLog.id)
        )
    )
    automatic_pause: datetime | None = None
    completed: datetime | None = None
    for row in rows:
        if row.action == "maintenance_pause" and row.actor == "rollout-safety-controller":
            automatic_pause = row.ts
        elif row.action == "maintenance_resume" and automatic_pause is not None:
            completed = row.ts
    return completed


def _report(
    destination_id: uuid.UUID,
    *,
    reasons: list[str],
) -> ProductionSafetyReport:
    return ProductionSafetyReport(
        status="blocked",
        destination_id=destination_id,
        policy_sha256=None,
        attempts=0,
        failed_attempts=0,
        effects=0,
        unknown_effects=0,
        failure_rate=None,
        unknown_effect_rate=None,
        rollback_drill_at=None,
        current_destination_paused=False,
        qualifies_for_canary=False,
        reasons=tuple(reasons),
    )


def _report_payload(report: ProductionSafetyReport) -> dict[str, object]:
    payload: dict[str, object] = asdict(report)
    payload["destination_id"] = str(report.destination_id)
    return payload


def _payload_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
