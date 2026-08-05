from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from cryo.capabilities import capability_for
from cryo.db.models import (
    AuditLog,
    Destination,
    EffectReceipt,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    Tenant,
)
from cryo.db.repositories.intents import IntentStore, bind_intent
from cryo.domain.enums import EngineType, JobStatus, Mode, OperationType
from cryo.domain.intents import (
    CapabilityRequirement,
    ContainmentAction,
    EffectState,
    IntentBinding,
    IntentBudget,
)
from cryo.domain.models import MaintenancePlan, Operation
from cryo.planner.serialization import hash_plan, plan_to_dict
from tests.auth_helpers import OPERATOR_HEADERS, VIEWER_HEADERS, auth_header


def test_safety_api_surfaces_ambiguous_effects(api_db) -> None:
    source = Source(
        id=uuid4(),
        name=f"safety-source-{uuid4()}",
        type="rest",
        config={},
    )
    destination = Destination(
        id=uuid4(),
        name=f"safety-destination-{uuid4()}",
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
    )
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode=Mode.STANDARD.value,
        status=JobStatus.QUEUED.value,
    )
    plan = MaintenancePlan(
        table_ref="iceberg.analytics.events",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, {}),),
    )
    capability = capability_for(EngineType.TRINO, OperationType.OPTIMIZE)
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    binding = IntentBinding(
        actor_type="test",
        actor_id="operator",
        source_id=source.id,
        table_id=table.id,
        table_ref=plan.table_ref,
        destination_id=destination.id,
        operation_scope=(OperationType.OPTIMIZE,),
        canonical_plan=plan_to_dict(plan),
        plan_hash=hash_plan(plan),
        idempotency_key=f"intent:{job.id}",
        expires_at=expires_at,
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
    with api_db.session_factory() as session:
        session.add_all([source, destination, table, run, job])
        session.commit()
        intent = bind_intent(session, job_id=job.id, binding=binding)
        session.commit()
        intent_id = str(intent.id)

    store = IntentStore(api_db.session_factory)
    now = expires_at.replace(tzinfo=None) - timedelta(minutes=30)
    attempt = store.begin_attempt(intent_id, worker_id="worker-a", now=now)
    receipt = store.prepare_effect(
        intent_id=intent_id,
        attempt_id=str(attempt.id),
        step_index=0,
        operation=OperationType.OPTIMIZE,
        strategy=capability.effect_strategy,
        destination_id=str(destination.id),
    )
    store.mark_effect_submitted(
        str(receipt.id),
        external_reference="query-42",
        now=now,
    )
    store.resolve_effect(
        str(receipt.id),
        EffectState.UNKNOWN,
        error={"message": "coordinator response lost"},
        now=now,
    )

    summary = api_db.client.get(
        "/api/v1/maintenance/safety/summary",
        headers=OPERATOR_HEADERS,
    )
    intents = api_db.client.get(
        "/api/v1/maintenance/safety/intents",
        headers=OPERATOR_HEADERS,
    )
    effects = api_db.client.get(
        "/api/v1/maintenance/safety/effects?ambiguous_only=true",
        headers=OPERATOR_HEADERS,
    )
    viewer_summary = api_db.client.get(
        "/api/v1/maintenance/safety/summary",
        headers=VIEWER_HEADERS,
    )

    assert summary.status_code == 200
    assert summary.json()["ambiguous_effects"] == 1
    assert summary.json()["active_attempts"] == 1
    assert intents.status_code == 200
    assert intents.json()[0]["plan_hash"] == binding.plan_hash
    assert effects.status_code == 200
    assert effects.json()[0]["state"] == "unknown"
    assert effects.json()[0]["external_reference"] == "query-42"
    assert viewer_summary.status_code == 200

    resolution = api_db.client.post(
        f"/api/v1/maintenance/safety/effects/{receipt.id}/resolve",
        headers=OPERATOR_HEADERS,
        json={
            "outcome": "succeeded",
            "reason": "Verified query-42 in the engine history",
            "evidence_reference": "query-42",
            "continue_job": True,
        },
    )

    assert resolution.status_code == 200
    assert resolution.json()["receipt"]["state"] == "succeeded"
    assert resolution.json()["continuation"] == "queued_for_admission"
    with api_db.session_factory() as session:
        persisted_job = session.get(MaintenanceJob, job.id)
        persisted_receipt = session.get(EffectReceipt, receipt.id)
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.job_id == job.id,
                AuditLog.action == "reconcile_maintenance_effect",
            )
        )
        assert persisted_job is not None
        assert persisted_job.status == JobStatus.QUEUED.value
        assert persisted_receipt is not None
        assert persisted_receipt.state == EffectState.SUCCEEDED.value
        assert audit is not None
        assert audit.actor == "test-user"
        assert "source=operator_attestation" in (audit.detail or "")

    replay = api_db.client.post(
        f"/api/v1/maintenance/safety/effects/{receipt.id}/resolve",
        headers=OPERATOR_HEADERS,
        json={
            "outcome": "succeeded",
            "reason": "Attempted duplicate resolution",
        },
    )
    assert replay.status_code == 409


def test_safety_api_isolates_tenant_intents_effects_and_resolution(api_db) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    with api_db.session_factory() as session:
        session.add_all(
            [
                Tenant(
                    id=tenant_a,
                    name=f"Tenant A {tenant_a}",
                    slug=f"tenant-a-{tenant_a}",
                    admin_user_id="tenant-a-admin",
                ),
                Tenant(
                    id=tenant_b,
                    name=f"Tenant B {tenant_b}",
                    slug=f"tenant-b-{tenant_b}",
                    admin_user_id="tenant-b-admin",
                ),
            ]
        )
        session.commit()

    intent_a, _ = _create_ambiguous_effect(api_db, tenant_a, "tenant-a")
    _, receipt_b = _create_ambiguous_effect(api_db, tenant_b, "tenant-b")
    headers = auth_header("operator", tenant_id=tenant_a)

    summary = api_db.client.get(
        "/api/v1/maintenance/safety/summary",
        headers=headers,
    )
    intents = api_db.client.get(
        "/api/v1/maintenance/safety/intents",
        headers=headers,
    )
    effects = api_db.client.get(
        "/api/v1/maintenance/safety/effects?ambiguous_only=true",
        headers=headers,
    )
    cross_tenant_resolution = api_db.client.post(
        f"/api/v1/maintenance/safety/effects/{receipt_b}/resolve",
        headers=headers,
        json={
            "outcome": "failed",
            "reason": "Must not resolve another tenant's effect",
        },
    )

    assert summary.status_code == 200
    assert summary.json()["ambiguous_effects"] == 1
    assert summary.json()["active_attempts"] == 1
    assert [row["id"] for row in intents.json()] == [str(intent_a)]
    assert len(effects.json()) == 1
    assert effects.json()[0]["intent_id"] == str(intent_a)
    assert cross_tenant_resolution.status_code == 404


def test_safety_api_batch_resolution_reports_each_durable_result(api_db) -> None:
    _, first_receipt = _create_ambiguous_effect(api_db, None, "batch-a")
    _, second_receipt = _create_ambiguous_effect(api_db, None, "batch-b")

    response = api_db.client.post(
        "/api/v1/maintenance/safety/effects/resolve-batch",
        headers=OPERATOR_HEADERS,
        json={
            "receipt_ids": [str(first_receipt), str(second_receipt)],
            "outcome": "succeeded",
            "reason": "Verified both query references in coordinator history",
            "continue_job": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["requested"] == 2
    assert response.json()["resolved"] == 2
    assert response.json()["failed"] == 0
    assert all(item["ok"] for item in response.json()["items"])
    with api_db.session_factory() as session:
        states = list(
            session.scalars(
                select(EffectReceipt.state).where(
                    EffectReceipt.id.in_([first_receipt, second_receipt])
                )
            )
        )
    assert states == [EffectState.SUCCEEDED.value, EffectState.SUCCEEDED.value]


def test_safety_api_batch_resolution_rejects_duplicate_receipts(api_db) -> None:
    _, receipt = _create_ambiguous_effect(api_db, None, "batch-duplicate")

    response = api_db.client.post(
        "/api/v1/maintenance/safety/effects/resolve-batch",
        headers=OPERATOR_HEADERS,
        json={
            "receipt_ids": [str(receipt), str(receipt)],
            "outcome": "failed",
            "reason": "Duplicate selection must not execute twice",
        },
    )

    assert response.status_code == 422


def _create_ambiguous_effect(api_db, tenant_id, suffix: str):
    source = Source(
        id=uuid4(),
        tenant_id=tenant_id,
        name=f"safety-source-{suffix}-{uuid4()}",
        type="rest",
        config={},
    )
    destination = Destination(
        id=uuid4(),
        tenant_id=tenant_id,
        name=f"safety-destination-{suffix}-{uuid4()}",
        engine=EngineType.TRINO.value,
        config={},
    )
    table = Table(
        id=uuid4(),
        tenant_id=tenant_id,
        source_id=source.id,
        schema_name="analytics",
        table_name=f"events_{suffix}",
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
        destination_id=destination.id,
        mode=Mode.STANDARD.value,
        status=JobStatus.QUEUED.value,
    )
    plan = MaintenancePlan(
        table_ref=f"iceberg.analytics.events_{suffix}",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, {}),),
    )
    capability = capability_for(EngineType.TRINO, OperationType.OPTIMIZE)
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    binding = IntentBinding(
        tenant_id=tenant_id,
        actor_type="test",
        actor_id="operator",
        source_id=source.id,
        table_id=table.id,
        table_ref=plan.table_ref,
        destination_id=destination.id,
        operation_scope=(OperationType.OPTIMIZE,),
        canonical_plan=plan_to_dict(plan),
        plan_hash=hash_plan(plan),
        idempotency_key=f"intent:{job.id}",
        expires_at=expires_at,
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
    with api_db.session_factory() as session:
        session.add_all([source, destination, table, run, job])
        session.commit()
        intent = bind_intent(session, job_id=job.id, binding=binding)
        session.commit()
        intent_id = str(intent.id)

    store = IntentStore(api_db.session_factory)
    now = expires_at.replace(tzinfo=None) - timedelta(minutes=30)
    attempt = store.begin_attempt(intent_id, worker_id=f"worker-{suffix}", now=now)
    receipt = store.prepare_effect(
        intent_id=intent_id,
        attempt_id=str(attempt.id),
        step_index=0,
        operation=OperationType.OPTIMIZE,
        strategy=capability.effect_strategy,
        destination_id=str(destination.id),
    )
    store.mark_effect_submitted(
        str(receipt.id),
        external_reference=f"query-{suffix}",
        now=now,
    )
    store.resolve_effect(
        str(receipt.id),
        EffectState.UNKNOWN,
        error={"message": "coordinator response lost"},
        now=now,
    )
    return intent.id, receipt.id
