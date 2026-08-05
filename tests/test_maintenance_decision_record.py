from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import (
    AuditLog,
    Destination,
    EffectReceipt,
    MaintenanceAttempt,
    MaintenanceIntent,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
)
from cryo.domain.enums import EngineType, JobStatus, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.evidence.mdr import MDR_SCHEMA_VERSION, build_maintenance_decision_record
from cryo.planner.serialization import hash_plan, plan_to_dict


def test_mdr_joins_lineage_and_redacts_sensitive_fields(session: Session) -> None:
    now = datetime.now(UTC)
    source = Source(id=uuid4(), name="source", type="rest", config={})
    destination = Destination(
        id=uuid4(),
        name="destination",
        engine=EngineType.TRINO.value,
        config={"password": "must-not-export"},
    )
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="private_schema",
        table_name="private_table",
        is_active=True,
    )
    run = MaintenanceRun(
        id=uuid4(),
        kind="adhoc",
        trigger="test",
        status=JobStatus.SUCCEEDED.value,
        summary={},
    )
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode=Mode.STANDARD.value,
        status=JobStatus.SUCCEEDED.value,
    )
    plan = MaintenancePlan(
        table_ref="iceberg.private_schema.private_table",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, {"target_file_size_bytes": 268435456}),),
    )
    intent = MaintenanceIntent(
        id=uuid4(),
        job_id=job.id,
        version=1,
        status="succeeded",
        actor_type="user",
        actor_id="alice@example.com",
        source_id=source.id,
        table_id=table.id,
        table_ref=plan.table_ref,
        destination_id=destination.id,
        operation_scope=[OperationType.OPTIMIZE.value],
        observed_snapshot_id=42,
        policy_id="default",
        policy_version="v1",
        canonical_plan=plan_to_dict(plan),
        plan_hash=hash_plan(plan),
        idempotency_key=f"intent:{job.id}",
        approval_bindings=[],
        fencing_epoch=3,
        budget={"max_bytes": 10_000},
        capability_requirements=[],
        containment_action="reconcile",
        expires_at=now + timedelta(hours=1),
    )
    attempt = MaintenanceAttempt(
        id=uuid4(),
        intent_id=intent.id,
        attempt_number=1,
        worker_id="worker-private",
        fencing_epoch=3,
        status="succeeded",
        started_at=now,
        heartbeat_at=now,
        completed_at=now + timedelta(seconds=2),
    )
    effect = EffectReceipt(
        id=uuid4(),
        intent_id=intent.id,
        attempt_id=attempt.id,
        destination_id=destination.id,
        step_index=0,
        operation_type=OperationType.OPTIMIZE.value,
        strategy="stable_reference_probe",
        state="succeeded",
        idempotency_key="secret-idempotency-key",
        external_reference="query-private-123",
        submission={"sql": "SECRET SQL"},
        result={"input_bytes": 1000, "output_bytes": 600, "raw_secret": "hide"},
        submitted_at=now,
        resolved_at=now + timedelta(seconds=2),
        probe_count=1,
    )
    audit = AuditLog(
        table_id=table.id,
        job_id=job.id,
        action=OperationType.OPTIMIZE.value,
        status="OK",
        engine=EngineType.TRINO.value,
        detail="SECRET SQL",
        duration_ms=2000,
        actual_bytes_freed=400,
        input_files_count=10,
        output_files_count=4,
    )
    session.add_all([source, destination, table, run, job, intent, attempt, effect, audit])
    session.commit()

    record = build_maintenance_decision_record(session, intent.id)
    serialized = str(record)

    assert record["schema_version"] == MDR_SCHEMA_VERSION
    assert record["decision"]["policy"] == {"id": "default", "version": "v1"}
    assert record["plan"]["hash"] == hash_plan(plan)
    assert record["attempts"][0]["status"] == "succeeded"
    assert record["effects"][0]["result_metrics"] == {
        "input_bytes": 1000,
        "output_bytes": 600,
    }
    assert record["outcomes"][0]["actual_bytes_freed"] == 400
    assert record["capabilities"][0]["support"] == "supported"
    assert len(record["integrity"]["record_sha256"]) == 64
    assert "alice@example.com" not in serialized
    assert "private_schema" not in serialized
    assert "private_table" not in serialized
    assert "SECRET SQL" not in serialized
    assert "raw_secret" not in serialized
    assert "must-not-export" not in serialized
