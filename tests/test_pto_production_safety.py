from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import (
    Destination,
    EffectReceipt,
    LayoutRecommendation,
    MaintenanceAttempt,
)
from cryo.db.repositories.intents import bind_intent, set_pause_state
from cryo.domain.intents import AttemptStatus, EffectState
from cryo.optimizer.production_safety import (
    evaluate_production_safety,
    is_production_safety_evidence_current,
    record_production_safety,
)
from tests.test_durable_intents import _persist_intent_fixture


def test_internal_rollout_records_qualify_production_safety(session: Session) -> None:
    job, binding = _persist_intent_fixture(session)
    intent = bind_intent(session, job_id=job.id, binding=binding)
    destination = session.get(Destination, job.destination_id)
    assert destination is not None
    destination.config = {
        "rollout_policy": {
            "enabled": True,
            "phase": "canary",
            "window_minutes": 60,
            "min_attempts": 1,
            "max_failure_rate": 0.05,
            "max_unknown_effect_rate": 0.01,
        }
    }
    now = datetime.now(UTC)
    attempt = MaintenanceAttempt(
        intent_id=intent.id,
        attempt_number=1,
        worker_id="worker-1",
        fencing_epoch=intent.fencing_epoch,
        status=AttemptStatus.SUCCEEDED.value,
        started_at=now - timedelta(minutes=5),
        heartbeat_at=now - timedelta(minutes=4),
        completed_at=now - timedelta(minutes=4),
    )
    session.add(attempt)
    session.flush()
    session.add(
        EffectReceipt(
            intent_id=intent.id,
            attempt_id=attempt.id,
            destination_id=destination.id,
            step_index=0,
            operation_type="optimize",
            strategy="destination_idempotency_token",
            state=EffectState.SUCCEEDED.value,
            idempotency_key="effect-1",
            created_at=now - timedelta(minutes=4),
        )
    )
    recommendation = LayoutRecommendation(
        id=uuid4(),
        table_id=job.table_id,
        recommended_strategy="binpack",
        recommended_columns=[],
        status="approved",
        detail={"source": "pto", "shadow": True},
    )
    session.add(recommendation)
    set_pause_state(
        session,
        scope_type="destination",
        scope_id=str(destination.id),
        paused=True,
        changed_by="rollout-safety-controller",
        reason="automatic canary halt: injected drill breach",
    )
    set_pause_state(
        session,
        scope_type="destination",
        scope_id=str(destination.id),
        paused=False,
        changed_by="operator-1",
        reason="drill recovery verified",
    )
    session.commit()

    report = record_production_safety(
        session,
        job.table_id,
        destination.id,
        actor="operator-1",
    )

    assert report.status == "qualified"
    assert report.qualifies_for_canary is True
    assert report.attempts == 1
    assert report.effects == 1
    assert report.rollback_drill_at is not None
    session.refresh(recommendation)
    evidence = (recommendation.detail or {})["production_safety"]
    assert evidence["schema"] == "production-safety/v1"
    assert is_production_safety_evidence_current(session, evidence) is True
    destination.config = {
        **destination.config,
        "rollout_policy": {**destination.config["rollout_policy"], "max_failure_rate": 0.01},
    }
    session.commit()
    assert is_production_safety_evidence_current(session, evidence) is False


def test_production_safety_rejects_policy_without_internal_evidence(session: Session) -> None:
    job, _binding = _persist_intent_fixture(session)
    destination = session.get(Destination, job.destination_id)
    assert destination is not None
    destination.config = {
        "rollout_policy": {
            "enabled": True,
            "phase": "canary",
            "window_minutes": 60,
            "min_attempts": 1,
        }
    }
    session.commit()

    report = evaluate_production_safety(session, destination.id)

    assert report.qualifies_for_canary is False
    assert "rollout_observing" in report.reasons
    assert "insufficient_effect_receipts" in report.reasons
    assert "missing_recent_automatic_halt_resume_drill" in report.reasons
