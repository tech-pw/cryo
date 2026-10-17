from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cryo.capabilities import capability_for
from cryo.db.models import (
    Destination,
    MaintenanceAttempt,
    MaintenanceFence,
    MaintenanceIntent,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
)
from cryo.db.repositories.intents import (
    EffectConflictError,
    IntentConflictError,
    IntentStore,
    MaintenancePausedError,
    bind_intent,
    set_pause_state,
)
from cryo.domain.enums import EngineType, JobStatus, Mode, OperationType
from cryo.domain.intents import (
    ApprovalBinding,
    AttemptStatus,
    CapabilityRequirement,
    ContainmentAction,
    EffectBudgetUsage,
    EffectState,
    IntentBinding,
    IntentBudget,
    IntentStatus,
    MaintenanceIntentRecord,
)
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.intents.binding import _capability_requirements, _idempotency_key
from cryo.intents.validation import IntentValidationError, validate_execution_intent
from cryo.planner.serialization import (
    canonical_plan_bytes,
    hash_plan,
    plan_from_dict,
    plan_to_dict,
)
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import (
    DestinationConfig,
    FakeMaintenanceEngine,
    InMemoryJobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
    Worker,
)


def test_canonical_plan_round_trip_and_hash_are_stable() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.analytics.events",
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                params={"target_file_size_mb": 512, "nested": {"b": 2, "a": 1}},
            ),
        ),
        notes=("bound",),
    )

    restored = plan_from_dict(plan_to_dict(plan))

    assert restored == plan
    assert canonical_plan_bytes(restored) == canonical_plan_bytes(plan)
    assert hash_plan(restored) == hash_plan(plan)


def test_intent_idempotency_key_covers_full_execution_contract() -> None:
    job_id = str(uuid4())
    contract = {
        "plan_hash": "a" * 64,
        "observed_snapshot_id": 42,
        "budget": {"max_output_files": 100},
        "capability_requirements": [
            {
                "destination_id": str(uuid4()),
                "engine": "trino",
                "operation": "optimize",
                "effect_strategy": "destination_idempotency_token",
            }
        ],
    }

    original = _idempotency_key(
        job_id=job_id,
        semantic_contract=contract,
    )
    reordered = _idempotency_key(
        job_id=job_id,
        semantic_contract=dict(reversed(contract.items())),
    )
    new_snapshot = _idempotency_key(
        job_id=job_id,
        semantic_contract={**contract, "observed_snapshot_id": 43},
    )
    tighter_budget = _idempotency_key(
        job_id=job_id,
        semantic_contract={
            **contract,
            "budget": {"max_output_files": 50},
        },
    )

    assert original == reordered
    assert original != new_snapshot
    assert original != tighter_budget


def test_execution_validation_rejects_snapshot_drift() -> None:
    plan, job, intent, stats = _bound_execution()
    stats["snapshot_watermark"] = 43

    with pytest.raises(IntentValidationError, match="snapshot_changed"):
        validate_execution_intent(
            intent,
            job=job,
            destination_engine=EngineType.TRINO,
            stats=stats,
        )

    assert hash_plan(plan) == intent.plan_hash


def test_worker_executes_bound_plan_without_replanning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, job, intent, stats = _bound_execution()
    queue, locks, store = _durable_worker_fixture(job=job, stats=stats)
    intents = _FakeIntentStore(intent)
    engine = FakeMaintenanceEngine()

    def fail_if_replanned(**_: object) -> MaintenancePlan:
        raise AssertionError("durable jobs must not be replanned")

    monkeypatch.setattr("cryo.worker.worker.build_execution_plan", fail_if_replanned)

    result = Worker(
        queue,
        store,
        lambda destination: engine,
        locks,
        intent_store=intents,
    ).process_next()

    assert result is not None
    assert result.status is JobStatus.SUCCEEDED
    assert [statement.action for statement in engine.executed] == [OperationType.OPTIMIZE]
    assert intents.finished_attempts == [AttemptStatus.SUCCEEDED]
    assert intents.receipt.state == EffectState.SUCCEEDED.value
    assert result.plan == plan_to_dict(plan)


def test_worker_keeps_ambiguous_effect_for_reconciliation() -> None:
    _, job, intent, stats = _bound_execution()
    queue, locks, store = _durable_worker_fixture(job=job, stats=stats)
    intents = _FakeIntentStore(intent)
    engine = FakeMaintenanceEngine(fail_actions={OperationType.OPTIMIZE})

    result = Worker(
        queue,
        store,
        lambda destination: engine,
        locks,
        intent_store=intents,
    ).process_next()

    assert result is not None
    assert result.status is JobStatus.PARTIAL
    assert result.error is not None and result.error.startswith("effect_unknown:")
    assert intents.receipt.state == EffectState.UNKNOWN.value
    assert intents.finished_attempts == [AttemptStatus.RECONCILE_REQUIRED]
    assert store.audit[-1].status == "UNKNOWN"


def test_worker_keeps_engine_reported_uncertain_outcome_for_reconciliation() -> None:
    _, job, intent, stats = _bound_execution()
    queue, locks, store = _durable_worker_fixture(job=job, stats=stats)
    intents = _FakeIntentStore(intent)

    result = Worker(
        queue,
        store,
        lambda destination: _UnknownOutcomeEngine(),
        locks,
        intent_store=intents,
    ).process_next()

    assert result is not None
    assert result.status is JobStatus.PARTIAL
    assert result.error == "effect_unknown:engine response timed out"
    assert intents.receipt.external_reference == "engine-op-42"
    assert intents.receipt.state == EffectState.UNKNOWN.value
    assert intents.finished_attempts == [AttemptStatus.RECONCILE_REQUIRED]
    assert store.audit[-1].status == "UNKNOWN"


def test_worker_defers_when_intent_effect_destination_is_paused() -> None:
    _, job, intent, stats = _bound_execution()
    queue, locks, store = _durable_worker_fixture(job=job, stats=stats)
    intents = _PausedIntentStore(intent)
    engine = FakeMaintenanceEngine()

    result = Worker(
        queue,
        store,
        lambda destination: engine,
        locks,
        intent_store=intents,
    ).process_next()

    assert result is not None
    assert result.status is JobStatus.DEFERRED
    assert "paused" in (result.error or "")
    assert engine.executed == []


def test_worker_defers_legacy_job_while_maintenance_is_paused() -> None:
    _, bound_job, _, stats = _bound_execution()
    job = dataclasses.replace(bound_job, intent_id=None)
    queue, locks, store = _durable_worker_fixture(job=job, stats=stats)
    engine = FakeMaintenanceEngine()
    paused = SimpleNamespace(is_paused=lambda **_: True)

    result = Worker(
        queue, store, lambda destination: engine, locks, intent_store=paused
    ).process_next()

    assert result is not None
    assert result.status is JobStatus.DEFERRED
    assert "paused" in (result.error or "")
    assert engine.executed == []


def test_worker_contains_follow_on_effects_after_output_budget_breach() -> None:
    plan, job, intent, stats = _bound_execution()
    second_operation = Operation(OperationType.OPTIMIZE_MANIFESTS, {})
    plan = MaintenancePlan(
        table_ref=plan.table_ref,
        mode=plan.mode,
        operations=(*plan.operations, second_operation),
    )
    manifest_capability = capability_for(
        EngineType.TRINO,
        OperationType.OPTIMIZE_MANIFESTS,
    )
    intent = intent.model_copy(
        update={
            "canonical_plan": plan_to_dict(plan),
            "plan_hash": hash_plan(plan),
            "operation_scope": (
                OperationType.OPTIMIZE,
                OperationType.OPTIMIZE_MANIFESTS,
            ),
            "budget": intent.budget.model_copy(update={"max_output_files": 1}),
            "capability_requirements": (
                *intent.capability_requirements,
                CapabilityRequirement(
                    destination_id=intent.destination_id,
                    engine=EngineType.TRINO,
                    operation=OperationType.OPTIMIZE_MANIFESTS,
                    minimum_maturity=manifest_capability.maturity,
                    effect_strategy=manifest_capability.effect_strategy,
                ),
            ),
        }
    )
    job.plan = plan_to_dict(plan)
    queue, locks, store = _durable_worker_fixture(job=job, stats=stats)
    intents = _FakeIntentStore(intent)
    engine = _BudgetBreachEngine()

    result = Worker(
        queue,
        store,
        lambda destination: engine,
        locks,
        intent_store=intents,
    ).process_next()

    assert result is not None
    assert result.status is JobStatus.PARTIAL
    assert (result.error or "").startswith("intent_budget_exceeded:output_files_budget_exceeded")
    assert [statement.action for statement in engine.executed] == [OperationType.OPTIMIZE]
    assert intents.receipt.state == EffectState.SUCCEEDED.value
    assert intents.finished_attempts == [AttemptStatus.FAILED]


def test_resumed_worker_applies_persisted_budget_before_next_effect() -> None:
    _, job, intent, stats = _bound_execution()
    intent = intent.model_copy(
        update={"budget": intent.budget.model_copy(update={"max_output_files": 1})}
    )
    queue, locks, store = _durable_worker_fixture(job=job, stats=stats)
    intents = _MultiEffectIntentStore(
        intent,
        initial_usage=EffectBudgetUsage(output_files=2),
    )
    engine = FakeMaintenanceEngine()

    result = Worker(
        queue,
        store,
        lambda destination: engine,
        locks,
        intent_store=intents,
    ).process_next()

    assert result is not None
    assert result.status is JobStatus.PARTIAL
    assert (result.error or "").startswith("intent_budget_exceeded:output_files_budget_exceeded")
    assert engine.executed == []
    assert intents.receipts == {}
    assert intents.finished_attempts == [AttemptStatus.FAILED]


def test_durable_native_bundle_stops_before_next_operation_on_budget_breach() -> None:
    operations = (
        Operation(OperationType.EXPIRE_SNAPSHOTS, {}),
        Operation(OperationType.REMOVE_ORPHAN_FILES, {}),
    )
    plan, job, intent, stats = _bound_execution(
        engine_type=EngineType.NATIVE,
        operations=operations,
    )
    intent = intent.model_copy(
        update={
            "budget": intent.budget.model_copy(update={"max_output_files": 1}),
            "approvals": tuple(_approval_for_operation(operation) for operation in operations),
        }
    )
    job.plan = plan_to_dict(plan)
    queue, locks, store = _durable_worker_fixture(
        job=job,
        stats=stats,
        engine_type=EngineType.NATIVE,
        destination_config={"engine_impl": "go"},
    )
    intents = _MultiEffectIntentStore(intent)
    engine = _NativeBudgetEngine()

    result = Worker(
        queue,
        store,
        lambda destination: engine,
        locks,
        intent_store=intents,
    ).process_next()

    assert result is not None
    assert result.status is JobStatus.PARTIAL
    assert (result.error or "").startswith("intent_budget_exceeded:output_files_budget_exceeded")
    assert engine.executed_plans == [(OperationType.EXPIRE_SNAPSHOTS,)]
    assert [receipt.state for receipt in intents.receipts.values()] == [EffectState.SUCCEEDED.value]
    assert intents.finished_attempts == [AttemptStatus.FAILED]


def test_durable_sql_fallback_stops_before_next_statement_on_budget_breach() -> None:
    operation = Operation(
        OperationType.OPTIMIZE,
        {"strategy": "zorder", "zorder_columns": ["user_id"]},
    )
    plan, job, intent, stats = _bound_execution(
        engine_type=EngineType.NATIVE,
        operations=(operation,),
    )
    fallback_id = uuid4()
    native_capability = capability_for(EngineType.NATIVE, operation.type)
    fallback_capability = capability_for(EngineType.SPARK_EMR, operation.type)
    intent = intent.model_copy(
        update={
            "budget": intent.budget.model_copy(update={"max_output_files": 1}),
            "capability_requirements": (
                CapabilityRequirement(
                    destination_id=intent.destination_id,
                    engine=EngineType.NATIVE,
                    operation=operation.type,
                    minimum_maturity=native_capability.maturity,
                    effect_strategy=native_capability.effect_strategy,
                    executes_effect=False,
                ),
                CapabilityRequirement(
                    destination_id=fallback_id,
                    engine=EngineType.SPARK_EMR,
                    operation=operation.type,
                    minimum_maturity=fallback_capability.maturity,
                    effect_strategy=fallback_capability.effect_strategy,
                ),
            ),
        }
    )
    job.plan = plan_to_dict(plan)
    queue, locks, store = _durable_worker_fixture(
        job=job,
        stats=stats,
        engine_type=EngineType.NATIVE,
        destination_config={
            "engine_impl": "go",
            "sql_fallback_destination_id": str(fallback_id),
        },
    )
    store.add_destination(DestinationConfig(str(fallback_id), engine=EngineType.SPARK_EMR))
    intents = _MultiEffectIntentStore(intent)
    native_engine = _NativeBudgetEngine(output_files_count=None)
    fallback_engine = _FallbackBudgetEngine()

    result = Worker(
        queue,
        store,
        lambda destination: (
            fallback_engine if destination.id == str(fallback_id) else native_engine
        ),
        locks,
        intent_store=intents,
    ).process_next()

    assert result is not None
    assert result.status is JobStatus.PARTIAL
    assert (result.error or "").startswith("intent_budget_exceeded:output_files_budget_exceeded")
    assert native_engine.executed_plans == []
    assert len(fallback_engine.executed) == 1
    assert [receipt.state for receipt in intents.receipts.values()] == [EffectState.SUCCEEDED.value]
    assert intents.finished_attempts == [AttemptStatus.FAILED]


def test_intent_binding_advances_fence_and_preserves_bound_fields(
    session: Session,
) -> None:
    job, binding = _persist_intent_fixture(session)

    first = bind_intent(session, job_id=job.id, binding=binding)
    session.commit()
    second = bind_intent(
        session,
        job_id=job.id,
        binding=binding.model_copy(update={"idempotency_key": f"{binding.idempotency_key}:v2"}),
    )
    session.commit()

    assert first.status == IntentStatus.SUPERSEDED.value
    assert first.fencing_epoch == 1
    assert second.version == 2
    assert second.parent_intent_id == first.id
    assert second.fencing_epoch == 2
    assert job.current_intent_id == second.id
    fence = session.get(MaintenanceFence, job.table_id)
    assert fence is not None
    assert fence.epoch == 2
    assert fence.owner_intent_id == second.id

    second.plan_hash = "0" * 64
    with pytest.raises(ValueError, match="bound fields are immutable"):
        session.flush()
    session.rollback()


def test_intent_store_excludes_parallel_attempts_and_ambiguous_replay(
    session: Session,
) -> None:
    job, binding = _persist_intent_fixture(session)
    intent = bind_intent(session, job_id=job.id, binding=binding)
    session.commit()
    store = IntentStore(
        sessionmaker(
            bind=session.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        )
    )
    now = binding.expires_at.replace(tzinfo=None) - timedelta(minutes=30)

    attempt = store.begin_attempt(str(intent.id), worker_id="worker-a", now=now)
    with pytest.raises(IntentConflictError, match="active execution attempt"):
        store.begin_attempt(str(intent.id), worker_id="worker-b", now=now)

    receipt = store.prepare_effect(
        intent_id=str(intent.id),
        attempt_id=str(attempt.id),
        step_index=0,
        operation=OperationType.OPTIMIZE,
        strategy=capability_for(EngineType.TRINO, OperationType.OPTIMIZE).effect_strategy,
        destination_id=str(binding.destination_id),
    )
    store.mark_effect_submitted(str(receipt.id), external_reference="query-42", now=now)
    backlog = store.reconciliation_backlog(now=now)
    effects = store.list_reconciliation_effects(now=now)

    assert backlog.count == 1
    assert backlog.oldest_created_at is not None
    assert len(effects) == 1
    assert effects[0].run_id == str(job.run_id)
    assert effects[0].correlation_id == "test-correlation"
    assert effects[0].created_at is not None

    with pytest.raises(EffectConflictError, match="must be reconciled"):
        store.prepare_effect(
            intent_id=str(intent.id),
            attempt_id=str(attempt.id),
            step_index=0,
            operation=OperationType.OPTIMIZE,
            strategy=capability_for(EngineType.TRINO, OperationType.OPTIMIZE).effect_strategy,
            destination_id=str(binding.destination_id),
        )

    attempts = list(
        session.scalars(select(MaintenanceAttempt).where(MaintenanceAttempt.intent_id == intent.id))
    )
    assert len(attempts) == 1


def test_expired_attempt_rejection_persists_terminal_intent_state(
    session: Session,
) -> None:
    job, binding = _persist_intent_fixture(session)
    intent = bind_intent(session, job_id=job.id, binding=binding)
    session.commit()
    store = IntentStore(
        sessionmaker(
            bind=session.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        )
    )

    with pytest.raises(IntentConflictError, match="expired"):
        store.begin_attempt(
            str(intent.id),
            worker_id="worker-after-expiry",
            now=binding.expires_at + timedelta(seconds=1),
        )

    session.expire_all()
    persisted = session.get(MaintenanceIntent, intent.id)
    assert persisted is not None
    assert persisted.status == IntentStatus.EXPIRED.value


def test_native_fallback_binds_and_honors_actual_effect_destination(
    session: Session,
) -> None:
    source = Source(
        id=uuid4(),
        name=f"fallback-source-{uuid4()}",
        type="rest",
        config={},
    )
    fallback = Destination(
        id=uuid4(),
        name=f"spark-fallback-{uuid4()}",
        engine=EngineType.SPARK_EMR.value,
        config={},
    )
    primary = Destination(
        id=uuid4(),
        name=f"native-primary-{uuid4()}",
        engine=EngineType.NATIVE.value,
        config={
            "engine_impl": "go",
            "sql_fallback_destination_id": str(fallback.id),
        },
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
        trigger="test",
        status=JobStatus.QUEUED.value,
    )
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=primary.id,
        mode=Mode.STANDARD.value,
        status=JobStatus.QUEUED.value,
    )
    session.add_all([source, fallback, primary, table, run, job])
    session.commit()
    plan = MaintenancePlan(
        table_ref="iceberg.analytics.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {"strategy": "zorder", "zorder_columns": ["user_id"]},
            ),
        ),
    )
    requirements = _capability_requirements(
        session,
        primary,
        plan,
        stats={"total_delete_files": 0},
    )

    assert [
        (
            requirement.engine,
            requirement.destination_id,
            requirement.executes_effect,
        )
        for requirement in requirements
    ] == [
        (EngineType.NATIVE, primary.id, False),
        (EngineType.SPARK_EMR, fallback.id, True),
    ]

    binding = IntentBinding(
        actor_type="test",
        actor_id="operator",
        source_id=source.id,
        table_id=table.id,
        table_ref=plan.table_ref,
        destination_id=primary.id,
        operation_scope=(OperationType.OPTIMIZE,),
        canonical_plan=plan_to_dict(plan),
        plan_hash=hash_plan(plan),
        idempotency_key=f"intent:{job.id}",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        budget=IntentBudget(),
        capability_requirements=requirements,
        containment_action=ContainmentAction.RECONCILE,
    )
    intent = bind_intent(session, job_id=job.id, binding=binding)
    set_pause_state(
        session,
        scope_type="destination",
        scope_id=str(fallback.id),
        paused=True,
        changed_by="test-admin",
        reason="fallback incident",
    )
    session.commit()
    store = IntentStore(
        sessionmaker(
            bind=session.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        )
    )

    with pytest.raises(MaintenancePausedError):
        store.begin_attempt(
            str(intent.id),
            worker_id="worker-a",
            now=binding.expires_at.replace(tzinfo=None) - timedelta(minutes=30),
        )


def test_prepared_effect_is_reclaimed_without_replay_after_worker_crash(
    session: Session,
) -> None:
    job, binding = _persist_intent_fixture(session)
    intent = bind_intent(session, job_id=job.id, binding=binding)
    session.commit()
    store = IntentStore(
        sessionmaker(
            bind=session.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        )
    )
    now = binding.expires_at.replace(tzinfo=None) - timedelta(minutes=30)
    first_attempt = store.begin_attempt(
        str(intent.id),
        worker_id="worker-a",
        now=now,
    )
    first_receipt = store.prepare_effect(
        intent_id=str(intent.id),
        attempt_id=str(first_attempt.id),
        step_index=0,
        operation=OperationType.OPTIMIZE,
        strategy=capability_for(
            EngineType.TRINO,
            OperationType.OPTIMIZE,
        ).effect_strategy,
        destination_id=str(binding.destination_id),
    )
    store.finish_attempt(
        str(first_attempt.id),
        AttemptStatus.DEFERRED,
        now=now,
    )

    second_attempt = store.begin_attempt(
        str(intent.id),
        worker_id="worker-b",
        now=now,
    )
    reclaimed = store.prepare_effect(
        intent_id=str(intent.id),
        attempt_id=str(second_attempt.id),
        step_index=0,
        operation=OperationType.OPTIMIZE,
        strategy=capability_for(
            EngineType.TRINO,
            OperationType.OPTIMIZE,
        ).effect_strategy,
        destination_id=str(binding.destination_id),
    )

    assert reclaimed.id == first_receipt.id
    assert reclaimed.attempt_id == second_attempt.id
    assert reclaimed.state == EffectState.PREPARED.value


def _bound_execution(
    *,
    engine_type: EngineType = EngineType.TRINO,
    operations: tuple[Operation, ...] | None = None,
) -> tuple[
    MaintenancePlan,
    MaintenanceJobRecord,
    MaintenanceIntentRecord,
    dict[str, object],
]:
    job_id = uuid4()
    run_id = uuid4()
    table_id = uuid4()
    destination_id = uuid4()
    intent_id = uuid4()
    source_id = uuid4()
    now = datetime.now(UTC)
    bound_operations = operations or (Operation(OperationType.OPTIMIZE, params={"target_mb": 512}),)
    plan = MaintenancePlan(
        table_ref="iceberg.analytics.events",
        mode=Mode.STANDARD,
        operations=bound_operations,
    )
    canonical_plan = plan_to_dict(plan)
    intent = MaintenanceIntentRecord(
        id=intent_id,
        job_id=job_id,
        tenant_id=None,
        actor_type="test",
        actor_id="operator",
        source_id=source_id,
        table_id=table_id,
        table_ref=plan.table_ref,
        destination_id=destination_id,
        operation_scope=tuple(operation.type for operation in bound_operations),
        observed_snapshot_id=42,
        policy_id="test-policy",
        policy_version="v1",
        canonical_plan=canonical_plan,
        plan_hash=hash_plan(plan),
        idempotency_key=f"intent:{job_id}",
        expires_at=now + timedelta(hours=1),
        budget=IntentBudget(
            max_input_bytes=20 * 1024**3,
            max_output_bytes=20 * 1024**3,
            max_input_files=1000,
            max_output_files=1000,
            max_partitions=100,
            max_duration_seconds=3600,
            max_objects_deleted=1000,
        ),
        capability_requirements=tuple(
            CapabilityRequirement(
                destination_id=destination_id,
                engine=engine_type,
                operation=operation.type,
                minimum_maturity=capability_for(
                    engine_type,
                    operation.type,
                ).maturity,
                effect_strategy=capability_for(
                    engine_type,
                    operation.type,
                ).effect_strategy,
            )
            for operation in bound_operations
        ),
        containment_action=ContainmentAction.RECONCILE,
        approvals=(),
        version=1,
        parent_intent_id=None,
        status=IntentStatus.READY,
        fencing_epoch=1,
        created_at=now,
        updated_at=now,
    )
    job = MaintenanceJobRecord(
        id=str(job_id),
        run_id=str(run_id),
        table_id=str(table_id),
        destination_id=str(destination_id),
        mode=Mode.STANDARD,
        status=JobStatus.ADMITTED,
        plan=canonical_plan,
        intent_id=str(intent_id),
        intent_status=IntentStatus.READY.value,
        intent_expires_at=intent.expires_at,
        fencing_epoch=1,
    )
    stats: dict[str, object] = {
        "table_ref": plan.table_ref,
        "schema_name": "analytics",
        "table_name": "events",
        "snapshot_watermark": 42,
        "total_size_gb": 10,
        "total_files": 100,
        "total_partitions": 10,
    }
    return plan, job, intent, stats


def _persist_intent_fixture(
    session: Session,
) -> tuple[MaintenanceJob, IntentBinding]:
    source = Source(
        id=uuid4(),
        name=f"intent-source-{uuid4()}",
        type="rest",
        config={},
    )
    destination = Destination(
        id=uuid4(),
        name=f"intent-destination-{uuid4()}",
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
        trigger="test",
        status=JobStatus.QUEUED.value,
        summary={"correlation_id": "test-correlation"},
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
    binding = IntentBinding(
        actor_type="test",
        actor_id="operator",
        source_id=source.id,
        table_id=table.id,
        table_ref=plan.table_ref,
        destination_id=destination.id,
        operation_scope=(OperationType.OPTIMIZE,),
        observed_snapshot_id=42,
        policy_id="test-policy",
        policy_version="v1",
        canonical_plan=plan_to_dict(plan),
        plan_hash=hash_plan(plan),
        idempotency_key=f"intent:{job.id}",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
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
    return job, binding


def _durable_worker_fixture(
    *,
    job: MaintenanceJobRecord,
    stats: dict[str, object],
    engine_type: EngineType = EngineType.TRINO,
    destination_config: dict[str, object] | None = None,
) -> tuple[InMemoryMaintenanceQueue, InMemoryLockStore, InMemoryJobStore]:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            job.destination_id,
            engine=engine_type,
            config=destination_config or {},
        )
    )
    store.add_table_data(job.table_id, TableMaintenanceData(stats=stats))
    store.add_job(job)
    queue.enqueue(job.id, job.destination_id)
    queue.admit(job.id, job.destination_id, "replica-1")
    return queue, locks, store


class _FakeIntentStore:
    def __init__(self, intent: MaintenanceIntentRecord) -> None:
        self.intent = intent
        self.receipt = SimpleNamespace(
            id=uuid4(),
            state=EffectState.PREPARED.value,
            external_reference=None,
        )
        self.finished_attempts: list[AttemptStatus] = []

    def get(self, intent_id: str) -> MaintenanceIntentRecord | None:
        return self.intent if intent_id == str(self.intent.id) else None

    def begin_attempt(self, intent_id: str, *, worker_id: str) -> SimpleNamespace:
        assert intent_id == str(self.intent.id)
        assert worker_id
        return SimpleNamespace(id=uuid4())

    def prepare_effect(self, **_: object) -> SimpleNamespace:
        return self.receipt

    def mark_effect_submitted(self, receipt_id: str, **_: object) -> None:
        assert receipt_id == str(self.receipt.id)
        self.receipt.state = EffectState.SUBMITTED.value

    def set_effect_external_reference(self, receipt_id: str, reference: str) -> None:
        assert receipt_id == str(self.receipt.id)
        self.receipt.external_reference = reference

    def resolve_effect(
        self,
        receipt_id: str,
        state: EffectState,
        **_: object,
    ) -> None:
        assert receipt_id == str(self.receipt.id)
        self.receipt.state = state.value

    def finish_attempt(
        self,
        attempt_id: str,
        status: AttemptStatus,
        **_: object,
    ) -> None:
        assert attempt_id
        self.finished_attempts.append(status)


class _UnknownOutcomeEngine(FakeMaintenanceEngine):
    def execute(
        self,
        stmt: RenderedStatement,
        ctx: ExecCtx | None = None,
    ) -> ExecResult:
        if ctx is not None and ctx.on_engine_ref is not None:
            ctx.on_engine_ref("engine-op-42")
        return ExecResult(
            ok=False,
            outcome_known=False,
            engine_ref="engine-op-42",
            detail=stmt.sql,
            error="engine response timed out",
        )


class _PausedIntentStore(_FakeIntentStore):
    def begin_attempt(self, intent_id: str, *, worker_id: str) -> SimpleNamespace:
        raise MaintenancePausedError("maintenance execution is paused")


class _BudgetBreachEngine(FakeMaintenanceEngine):
    def execute(
        self,
        stmt: RenderedStatement,
        ctx: ExecCtx | None = None,
    ) -> ExecResult:
        self.executed.append(stmt)
        return ExecResult(
            ok=True,
            engine_ref="budget-effect-1",
            detail=stmt.sql,
            output_files_count=2,
        )


class _MultiEffectIntentStore:
    def __init__(
        self,
        intent: MaintenanceIntentRecord,
        *,
        initial_usage: EffectBudgetUsage | None = None,
    ) -> None:
        self.intent = intent
        self.receipts: dict[int, SimpleNamespace] = {}
        self.finished_attempts: list[AttemptStatus] = []
        self.usage = initial_usage or EffectBudgetUsage()

    def get(self, intent_id: str) -> MaintenanceIntentRecord | None:
        return self.intent if intent_id == str(self.intent.id) else None

    def begin_attempt(self, intent_id: str, *, worker_id: str) -> SimpleNamespace:
        assert intent_id == str(self.intent.id)
        assert worker_id
        return SimpleNamespace(id=uuid4())

    def budget_usage(self, intent_id: str) -> EffectBudgetUsage:
        assert intent_id == str(self.intent.id)
        return self.usage

    def prepare_effect(self, **kwargs: object) -> SimpleNamespace:
        step_index = int(kwargs["step_index"])
        receipt = self.receipts.get(step_index)
        if receipt is None:
            receipt = SimpleNamespace(
                id=uuid4(),
                state=EffectState.PREPARED.value,
                external_reference=None,
            )
            self.receipts[step_index] = receipt
        return receipt

    def mark_effect_submitted(self, receipt_id: str, **_: object) -> None:
        self._receipt(receipt_id).state = EffectState.SUBMITTED.value

    def set_effect_external_reference(self, receipt_id: str, reference: str) -> None:
        self._receipt(receipt_id).external_reference = reference

    def resolve_effect(
        self,
        receipt_id: str,
        state: EffectState,
        *,
        result: dict[str, object] | None = None,
        **_: object,
    ) -> None:
        receipt = self._receipt(receipt_id)
        was_succeeded = receipt.state == EffectState.SUCCEEDED.value
        receipt.state = state.value
        if state is EffectState.SUCCEEDED and not was_succeeded and result is not None:
            self.usage = self.usage.add(
                output_bytes=_optional_int(result.get("actual_bytes_written")),
                output_files=_optional_int(result.get("output_files_count")),
                duration_ms=_optional_int(result.get("duration_ms")),
                objects_deleted=_optional_int(result.get("objects_deleted")),
            )

    def finish_attempt(
        self,
        attempt_id: str,
        status: AttemptStatus,
        **_: object,
    ) -> None:
        assert attempt_id
        self.finished_attempts.append(status)

    def _receipt(self, receipt_id: str) -> SimpleNamespace:
        return next(receipt for receipt in self.receipts.values() if str(receipt.id) == receipt_id)


class _NativeBudgetEngine:
    type = EngineType.NATIVE

    def __init__(self, *, output_files_count: int | None = 2) -> None:
        self.output_files_count = output_files_count
        self.executed_plans: list[tuple[OperationType, ...]] = []

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]:
        return [
            RenderedStatement(
                action=operation.type,
                sql=f"NATIVE {operation.type.value}",
                engine=EngineType.NATIVE,
            )
            for operation in plan.operations
        ]

    def execute_plan(
        self,
        plan: MaintenancePlan,
        ctx: ExecCtx | None = None,
    ) -> list[ExecResult]:
        operations = tuple(operation.type for operation in plan.operations)
        self.executed_plans.append(operations)
        return [
            ExecResult(
                ok=True,
                engine_ref=f"native-{operation.value}",
                detail=operation.value,
                duration_ms=100,
                output_files_count=self.output_files_count,
            )
            for operation in operations
        ]


class _FallbackBudgetEngine(FakeMaintenanceEngine):
    def __init__(self) -> None:
        super().__init__(EngineType.SPARK_EMR)

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]:
        operation = plan.operations[0]
        return [
            RenderedStatement(
                action=operation.type,
                sql=f"FALLBACK {index}",
                engine=EngineType.SPARK_EMR,
            )
            for index in range(2)
        ]

    def execute(
        self,
        stmt: RenderedStatement,
        ctx: ExecCtx | None = None,
    ) -> ExecResult:
        self.executed.append(stmt)
        return ExecResult(
            ok=True,
            engine_ref=f"fallback-{len(self.executed)}",
            detail=stmt.sql,
            duration_ms=100,
            output_files_count=2,
        )


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _approval_for_operation(operation: Operation) -> ApprovalBinding:
    now = datetime.now(UTC)
    params = json.dumps(
        operation.params,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ApprovalBinding(
        approval_id=uuid4(),
        approved_by="test-operator",
        approved_at=now,
        expires_at=now + timedelta(hours=1),
        operation=operation.type,
        params_hash=hashlib.sha256(params).hexdigest(),
    )
