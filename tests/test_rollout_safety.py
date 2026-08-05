from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceAttempt,
    MaintenanceControlState,
)
from cryo.db.repositories.intents import bind_intent
from cryo.domain.intents import AttemptStatus
from cryo.rollout.safety import (
    RolloutDecision,
    RolloutObservation,
    RolloutPolicy,
    evaluate_rollout,
    run_rollout_safety_tick,
)
from tests.test_durable_intents import _persist_intent_fixture


def test_rollout_observes_until_minimum_sample() -> None:
    result = evaluate_rollout(
        RolloutPolicy(min_attempts=20),
        RolloutObservation(attempts=19, failed_attempts=19, effects=19, unknown_effects=19),
    )

    assert result.decision is RolloutDecision.OBSERVING
    assert result.reason == "waiting for 1 more attempts"


def test_rollout_halts_when_attempt_failure_budget_is_breached() -> None:
    result = evaluate_rollout(
        RolloutPolicy(min_attempts=20, max_failure_rate=0.05),
        RolloutObservation(attempts=20, failed_attempts=2, effects=20, unknown_effects=0),
    )

    assert result.decision is RolloutDecision.HALT
    assert result.failure_rate == 0.1
    assert "failure rate" in result.reason


def test_rollout_halts_when_unknown_effect_budget_is_breached() -> None:
    result = evaluate_rollout(
        RolloutPolicy(min_attempts=10, max_unknown_effect_rate=0.01),
        RolloutObservation(attempts=10, failed_attempts=0, effects=10, unknown_effects=1),
    )

    assert result.decision is RolloutDecision.HALT
    assert result.unknown_effect_rate == 0.1
    assert "unknown effect rate" in result.reason


def test_rollout_remains_healthy_inside_declared_budgets() -> None:
    result = evaluate_rollout(
        RolloutPolicy(
            min_attempts=100,
            max_failure_rate=0.05,
            max_unknown_effect_rate=0.02,
        ),
        RolloutObservation(
            attempts=100,
            failed_attempts=5,
            effects=100,
            unknown_effects=2,
        ),
    )

    assert result.decision is RolloutDecision.HEALTHY


def test_rollout_tick_durably_pauses_breaching_destination(session) -> None:
    now = datetime.now(UTC)
    job, binding = _persist_intent_fixture(session)
    intent = bind_intent(session, job_id=job.id, binding=binding)
    destination = session.get(Destination, job.destination_id)
    assert destination is not None
    destination.config = {
        "rollout_policy": {
            "enabled": True,
            "phase": "canary",
            "window_minutes": 60,
            "min_attempts": 2,
            "max_failure_rate": 0.1,
            "max_unknown_effect_rate": 0.1,
        }
    }
    session.add_all(
        [
            MaintenanceAttempt(
                intent_id=intent.id,
                attempt_number=attempt_number,
                worker_id=f"worker-{attempt_number}",
                fencing_epoch=intent.fencing_epoch,
                status=AttemptStatus.FAILED.value,
                started_at=now,
                heartbeat_at=now,
                completed_at=now,
                error={"message": "forced regression"},
            )
            for attempt_number in (1, 2)
        ]
    )
    session.commit()

    result = run_rollout_safety_tick(session, now=now)

    assert result.halted == 1
    control = session.scalar(
        select(MaintenanceControlState).where(
            MaintenanceControlState.scope_type == "destination",
            MaintenanceControlState.scope_id == str(destination.id),
        )
    )
    assert control is not None
    assert control.paused is True
    assert control.changed_by == "rollout-safety-controller"
    audit = session.scalar(select(AuditLog).where(AuditLog.action == "maintenance_pause"))
    assert audit is not None
    assert "automatic canary halt" in (audit.detail or "")
