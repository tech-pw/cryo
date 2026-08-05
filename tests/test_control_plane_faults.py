from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from cryo.capabilities import capability_for
from cryo.db.models import (
    Destination,
    MaintenanceAttempt,
    MaintenanceIntent,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
)
from cryo.db.repositories import intents as intent_repository
from cryo.db.repositories.intents import IntentExpiredError, IntentStore, bind_intent
from cryo.db.repositories.job_store import SQLJobStore
from cryo.domain.enums import EngineType, JobStatus, Mode, OperationType
from cryo.domain.intents import (
    CapabilityRequirement,
    ContainmentAction,
    IntentBinding,
    IntentBudget,
    IntentStatus,
)
from cryo.domain.models import MaintenancePlan, Operation
from cryo.planner.serialization import hash_plan, plan_to_dict
from cryo.queue.reconcile import reconcile_queued_jobs
from cryo.queue.redis_queue import InMemoryMaintenanceQueue


def test_interrupted_transition_rolls_back_and_retry_converges(session: Session) -> None:
    job, binding = _persist_queued_job(session)
    intent = bind_intent(session, job_id=job.id, binding=binding)
    session.commit()
    factory = _session_factory(session)
    fail_next_commit = True

    @event.listens_for(factory, "before_commit")
    def interrupt_commit(_: Session) -> None:
        nonlocal fail_next_commit
        if fail_next_commit:
            fail_next_commit = False
            raise OperationalError("COMMIT", {}, ConnectionError("simulated failover"))

    store = IntentStore(factory)
    with pytest.raises(OperationalError):
        store.begin_attempt(str(intent.id), worker_id="worker-before-failover")

    session.expire_all()
    assert session.scalar(select(func.count()).select_from(MaintenanceAttempt)) == 0
    assert session.get(MaintenanceIntent, intent.id).status == IntentStatus.READY.value

    attempt = store.begin_attempt(str(intent.id), worker_id="worker-after-failover")

    assert attempt.attempt_number == 1
    assert store.get(str(intent.id)).status is IntentStatus.EXECUTING


def test_lost_queue_is_rebuilt_from_durable_job_state(session: Session) -> None:
    job, _ = _persist_queued_job(session)
    queue_after_loss = InMemoryMaintenanceQueue()

    rebuilt = reconcile_queued_jobs(SQLJobStore(_session_factory(session)), queue_after_loss)

    assert rebuilt == 1
    assert queue_after_loss.pending(str(job.destination_id)) == [str(job.id)]
    assert queue_after_loss.status(str(job.id)) is JobStatus.QUEUED
    assert reconcile_queued_jobs(SQLJobStore(_session_factory(session)), queue_after_loss) == 0


def test_database_time_controls_expiry_despite_worker_clock_skew(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_now = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    job, binding = _persist_queued_job(
        session,
        expires_at=database_now + timedelta(minutes=1),
    )
    intent = bind_intent(session, job_id=job.id, binding=binding)
    session.commit()
    store = IntentStore(_session_factory(session))
    monkeypatch.setattr(intent_repository, "_database_now", lambda _: database_now)

    attempt = store.begin_attempt(str(intent.id), worker_id="worker-with-skewed-clock")

    assert attempt.started_at.replace(tzinfo=UTC) == database_now


def test_database_time_rejects_expired_intent(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_now = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    job, binding = _persist_queued_job(
        session,
        expires_at=database_now - timedelta(seconds=1),
    )
    intent = bind_intent(session, job_id=job.id, binding=binding)
    session.commit()
    store = IntentStore(_session_factory(session))
    monkeypatch.setattr(intent_repository, "_database_now", lambda _: database_now)

    with pytest.raises(IntentExpiredError):
        store.begin_attempt(str(intent.id), worker_id="worker-with-skewed-clock")

    assert store.get(str(intent.id)).status is IntentStatus.EXPIRED


def _session_factory(session: Session) -> sessionmaker[Session]:
    return sessionmaker(
        bind=session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
    )


def _persist_queued_job(
    session: Session,
    *,
    expires_at: datetime | None = None,
) -> tuple[MaintenanceJob, IntentBinding]:
    source = Source(
        id=uuid4(),
        name=f"fault-source-{uuid4()}",
        type="rest",
        config={},
    )
    destination = Destination(
        id=uuid4(),
        name=f"fault-destination-{uuid4()}",
        engine=EngineType.TRINO.value,
        config={},
    )
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name="events",
        is_active=True,
    )
    run = MaintenanceRun(
        id=uuid4(),
        kind="adhoc",
        trigger="fault-test",
        status=JobStatus.QUEUED.value,
    )
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode=Mode.STANDARD.value,
        status=JobStatus.QUEUED.value,
    )
    session.add_all([source, destination, table, run, job])
    session.commit()

    plan = MaintenancePlan(
        table_ref="iceberg.analytics.events",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, {}),),
    )
    capability = capability_for(EngineType.TRINO, OperationType.OPTIMIZE)
    return job, IntentBinding(
        actor_type="test",
        actor_id="operator",
        source_id=source.id,
        table_id=table.id,
        table_ref=plan.table_ref,
        destination_id=destination.id,
        operation_scope=(OperationType.OPTIMIZE,),
        observed_snapshot_id=42,
        canonical_plan=plan_to_dict(plan),
        plan_hash=hash_plan(plan),
        idempotency_key=f"fault-intent:{job.id}",
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
        budget=IntentBudget(),
        capability_requirements=(
            CapabilityRequirement(
                destination_id=destination.id,
                engine=EngineType.TRINO,
                operation=OperationType.OPTIMIZE,
                minimum_maturity=capability.maturity,
                effect_strategy=capability.effect_strategy,
            ),
        ),
        containment_action=ContainmentAction.RECONCILE,
    )
