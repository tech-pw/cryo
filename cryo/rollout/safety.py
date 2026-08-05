from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import (
    Destination,
    EffectReceipt,
    MaintenanceAttempt,
    MaintenanceControlState,
    MaintenanceIntent,
)
from cryo.db.repositories.intents import set_pause_state
from cryo.domain.intents import AttemptStatus, EffectState
from cryo.observability.metrics import rollout_decisions_total


class RolloutDecision(StrEnum):
    OBSERVING = "observing"
    HEALTHY = "healthy"
    HALT = "halt"


class RolloutPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    phase: str = Field(default="canary", min_length=1, max_length=32)
    window_minutes: int = Field(default=60, ge=5, le=10_080)
    min_attempts: int = Field(default=20, ge=1, le=1_000_000)
    max_failure_rate: float = Field(default=0.05, ge=0, le=1)
    max_unknown_effect_rate: float = Field(default=0.01, ge=0, le=1)


@dataclass(frozen=True)
class RolloutObservation:
    attempts: int
    failed_attempts: int
    effects: int
    unknown_effects: int


@dataclass(frozen=True)
class RolloutEvaluation:
    decision: RolloutDecision
    reason: str
    failure_rate: float
    unknown_effect_rate: float


@dataclass(frozen=True)
class RolloutTickResult:
    evaluated: int = 0
    halted: int = 0
    invalid: int = 0


def evaluate_rollout(
    policy: RolloutPolicy,
    observation: RolloutObservation,
) -> RolloutEvaluation:
    failure_rate = _rate(observation.failed_attempts, observation.attempts)
    unknown_effect_rate = _rate(observation.unknown_effects, observation.effects)
    if observation.attempts < policy.min_attempts:
        return RolloutEvaluation(
            decision=RolloutDecision.OBSERVING,
            reason=f"waiting for {policy.min_attempts - observation.attempts} more attempts",
            failure_rate=failure_rate,
            unknown_effect_rate=unknown_effect_rate,
        )

    breaches: list[str] = []
    if failure_rate > policy.max_failure_rate:
        breaches.append(
            f"attempt failure rate {failure_rate:.4f} exceeds {policy.max_failure_rate:.4f}"
        )
    if unknown_effect_rate > policy.max_unknown_effect_rate:
        breaches.append(
            f"unknown effect rate {unknown_effect_rate:.4f} exceeds "
            f"{policy.max_unknown_effect_rate:.4f}"
        )
    if breaches:
        return RolloutEvaluation(
            decision=RolloutDecision.HALT,
            reason="; ".join(breaches),
            failure_rate=failure_rate,
            unknown_effect_rate=unknown_effect_rate,
        )
    return RolloutEvaluation(
        decision=RolloutDecision.HEALTHY,
        reason="cohort remains within declared failure budgets",
        failure_rate=failure_rate,
        unknown_effect_rate=unknown_effect_rate,
    )


def run_rollout_safety_tick(
    session: Session,
    *,
    now: datetime | None = None,
) -> RolloutTickResult:
    current = now or datetime.now(UTC)
    evaluated = 0
    halted = 0
    invalid = 0
    destinations = list(session.scalars(select(Destination).where(Destination.enabled.is_(True))))
    for destination in destinations:
        raw_policy = (destination.config or {}).get("rollout_policy")
        if raw_policy is None:
            continue
        try:
            policy = RolloutPolicy.model_validate(raw_policy)
        except ValidationError:
            invalid += 1
            rollout_decisions_total.labels(phase="invalid", decision="invalid").inc()
            continue
        if not policy.enabled:
            continue

        observation = _load_observation(
            session,
            destination_id=destination.id,
            cutoff=current - timedelta(minutes=policy.window_minutes),
        )
        evaluation = evaluate_rollout(policy, observation)
        evaluated += 1
        rollout_decisions_total.labels(
            phase=policy.phase,
            decision=evaluation.decision.value,
        ).inc()
        if evaluation.decision is not RolloutDecision.HALT:
            continue
        if _destination_is_paused(session, destination.id):
            continue

        set_pause_state(
            session,
            scope_type="destination",
            scope_id=str(destination.id),
            paused=True,
            changed_by="rollout-safety-controller",
            reason=f"automatic {policy.phase} halt: {evaluation.reason}",
        )
        session.commit()
        halted += 1

    return RolloutTickResult(evaluated=evaluated, halted=halted, invalid=invalid)


def _load_observation(
    session: Session,
    *,
    destination_id: UUID,
    cutoff: datetime,
) -> RolloutObservation:
    terminal_statuses = (
        AttemptStatus.SUCCEEDED.value,
        AttemptStatus.FAILED.value,
        AttemptStatus.RECONCILE_REQUIRED.value,
        AttemptStatus.FENCED.value,
    )
    attempts, failed_attempts = session.execute(
        select(
            func.count(MaintenanceAttempt.id),
            func.count(MaintenanceAttempt.id).filter(
                MaintenanceAttempt.status.in_(
                    (
                        AttemptStatus.FAILED.value,
                        AttemptStatus.RECONCILE_REQUIRED.value,
                    )
                )
            ),
        )
        .join(MaintenanceIntent, MaintenanceIntent.id == MaintenanceAttempt.intent_id)
        .where(
            MaintenanceIntent.destination_id == destination_id,
            MaintenanceAttempt.started_at >= cutoff,
            MaintenanceAttempt.status.in_(terminal_statuses),
        )
    ).one()
    effects, unknown_effects = session.execute(
        select(
            func.count(EffectReceipt.id),
            func.count(EffectReceipt.id).filter(EffectReceipt.state == EffectState.UNKNOWN.value),
        ).where(
            EffectReceipt.destination_id == destination_id,
            EffectReceipt.created_at >= cutoff,
        )
    ).one()
    return RolloutObservation(
        attempts=int(attempts or 0),
        failed_attempts=int(failed_attempts or 0),
        effects=int(effects or 0),
        unknown_effects=int(unknown_effects or 0),
    )


def load_rollout_observation(
    session: Session,
    *,
    destination_id: UUID,
    cutoff: datetime,
) -> RolloutObservation:
    return _load_observation(session, destination_id=destination_id, cutoff=cutoff)


def _destination_is_paused(session: Session, destination_id: UUID) -> bool:
    return (
        session.scalar(
            select(MaintenanceControlState.paused).where(
                MaintenanceControlState.scope_type == "destination",
                MaintenanceControlState.scope_id == str(destination_id),
            )
        )
        is True
    )


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
