from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceIntent,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableCostHistory,
    TableStatsHistory,
)
from cryo.domain.enums import EngineType, JobStatus, Mode
from cryo.intelligence.outcomes import OUTCOME_SCHEMA_VERSION, build_outcome_dataset


def test_dataset_projects_only_nearest_history_fields(session: Session) -> None:
    event_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    table, job, _ = _lineage_fixture(session)
    session.add(
        AuditLog(
            ts=event_at,
            table_id=table.id,
            job_id=job.id,
            action="optimize",
            status="OK",
            actual_bytes_freed=100,
        )
    )
    for hours in range(-100, 101):
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=event_at + timedelta(hours=hours),
                small_files_count=100 + hours,
                total_files=300,
                total_size_gb=10,
                raw={"reclaim_optimize_bytes": 200, "unused_file_metadata": "x" * 10000},
            )
        )
    session.commit()
    session.expunge_all()
    loaded = []
    event.listen(session, "loaded_as_persistent", lambda _, obj: loaded.append(type(obj)))
    dataset = build_outcome_dataset(session)
    row = dataset.rows[0]
    assert row.observation_at == event_at.replace(tzinfo=None)
    assert row.post_scan_at == (event_at + timedelta(hours=1)).replace(tzinfo=None)
    assert row.predicted_bytes_freed == 200
    assert row.pre_small_files_count == 100
    assert row.post_small_files_count == 101
    # No full history ORM object (and therefore no large raw blob) reaches Python.
    assert TableStatsHistory not in loaded


def test_observation_windows_apply_to_each_audit(session: Session) -> None:
    event_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    table, job, _ = _lineage_fixture(session)
    for days in (0, 30):
        session.add(
            AuditLog(
                ts=event_at + timedelta(days=days),
                table_id=table.id,
                job_id=job.id,
                action="optimize",
                status="OK",
                actual_bytes_freed=100,
            )
        )
    session.add(
        TableStatsHistory(
            table_id=table.id,
            scan_at=event_at + timedelta(days=20),
            raw={"reclaim_optimize_bytes": 200},
        )
    )
    session.commit()
    rows = build_outcome_dataset(session, observation_lookback_days=7).rows
    assert rows[0].post_scan_at is None
    assert rows[1].observation_at is None


def test_outcome_dataset_joins_frozen_prediction_effect_and_post_scan(session: Session) -> None:
    event_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    table, job, intent = _lineage_fixture(session)
    session.add_all(
        [
            TableStatsHistory(
                table_id=table.id,
                scan_at=event_at - timedelta(hours=1),
                small_files_count=120,
                total_files=150,
                total_size_gb=10.0,
                grade="C",
                overall_score=60,
                raw={
                    "reclaim_optimize_bytes": 8_000,
                    "delete_file_pct": 12.5,
                },
            ),
            TableCostHistory(
                table_id=table.id,
                scan_at=event_at - timedelta(hours=1),
                current_monthly_cost_usd=10.0,
                projected_savings_usd_month=2.5,
                reclaimable_bytes=9_000,
                precision="metadata",
            ),
            AuditLog(
                ts=event_at,
                table_id=table.id,
                job_id=job.id,
                action="optimize",
                status="OK",
                engine=EngineType.TRINO.value,
                actual_bytes_freed=6_000,
                input_files_count=120,
                output_files_count=12,
            ),
            TableStatsHistory(
                table_id=table.id,
                scan_at=event_at + timedelta(hours=1),
                small_files_count=8,
                total_files=38,
                total_size_gb=9.9,
                grade="A",
                overall_score=94,
                raw={"delete_file_pct": 1.0},
            ),
        ]
    )
    session.commit()

    dataset = build_outcome_dataset(session)
    row = dataset.rows[0]
    calibration = row.calibration_row()

    assert row.intent_id == intent.id
    assert row.predicted_bytes_freed == 8_000
    assert row.actual_bytes_freed == 6_000
    assert row.pre_small_files_count == 120
    assert row.post_small_files_count == 8
    assert row.lineage_complete is True
    assert dataset.completeness == 1.0
    assert calibration is not None
    assert calibration["schema_version"] == OUTCOME_SCHEMA_VERSION
    assert calibration["predicted_bytes_freed"] != calibration["actual_bytes_freed"]


def test_outcome_dataset_carries_real_p95_query_latency_when_scanner_wrote_it(
    session: Session,
) -> None:
    """cryo.scanner.persistence now writes a real p95_query_latency_ms into
    TableStatsHistory.raw (previously never populated by anything -- see
    _table_p95_query_latency_ms) -- confirms build_outcome_dataset's existing _raw_float read
    of that key actually surfaces a real value end-to-end, not just that the read path itself
    doesn't crash on a present key."""
    event_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    table, job, intent = _lineage_fixture(session)
    session.add_all(
        [
            TableStatsHistory(
                table_id=table.id,
                scan_at=event_at - timedelta(hours=1),
                small_files_count=120,
                total_files=150,
                total_size_gb=10.0,
                grade="C",
                overall_score=60,
                raw={"reclaim_optimize_bytes": 8_000, "p95_query_latency_ms": 850.0},
            ),
            AuditLog(
                ts=event_at,
                table_id=table.id,
                job_id=job.id,
                action="optimize",
                status="OK",
                engine=EngineType.TRINO.value,
                actual_bytes_freed=6_000,
                input_files_count=120,
                output_files_count=12,
            ),
            TableStatsHistory(
                table_id=table.id,
                scan_at=event_at + timedelta(hours=1),
                small_files_count=8,
                total_files=38,
                total_size_gb=9.9,
                grade="A",
                overall_score=94,
                raw={"p95_query_latency_ms": 310.0},
            ),
        ]
    )
    session.commit()

    row = build_outcome_dataset(session).rows[0]

    assert row.pre_p95_query_latency_ms == 850.0
    assert row.post_p95_query_latency_ms == 310.0
    # The actual point of the feature: a real, non-None before/after delta is now computable.
    assert row.pre_p95_query_latency_ms - row.post_p95_query_latency_ms == 540.0


def test_outcome_dataset_reports_missing_post_scan_without_claiming_completeness(
    session: Session,
) -> None:
    event_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    table, job, _ = _lineage_fixture(session)
    session.add_all(
        [
            TableStatsHistory(
                table_id=table.id,
                scan_at=event_at - timedelta(minutes=5),
                small_files_count=10,
                total_files=20,
                total_size_gb=1.0,
                grade="B",
                overall_score=80,
                raw={"reclaim_optimize_bytes": 1_000},
            ),
            AuditLog(
                ts=event_at,
                table_id=table.id,
                job_id=job.id,
                action="optimize",
                status="OK",
                actual_bytes_freed=900,
            ),
        ]
    )
    session.commit()

    dataset = build_outcome_dataset(session)

    assert dataset.eligible_count == 1
    assert dataset.complete_count == 0
    assert dataset.completeness == 0.0
    assert dataset.calibration_rows() == []


def test_outcome_dataset_bounds_history_and_abstains_outside_windows(session: Session) -> None:
    event_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    table, job, _ = _lineage_fixture(session)
    session.add_all(
        [
            TableStatsHistory(
                table_id=table.id,
                scan_at=event_at - timedelta(days=91),
                small_files_count=20,
                total_files=25,
                total_size_gb=2.0,
                raw={"reclaim_optimize_bytes": 1_000},
            ),
            AuditLog(
                ts=event_at,
                table_id=table.id,
                job_id=job.id,
                action="optimize",
                status="OK",
                actual_bytes_freed=900,
            ),
            TableStatsHistory(
                table_id=table.id,
                scan_at=event_at + timedelta(days=8),
                small_files_count=2,
                total_files=5,
                total_size_gb=1.8,
                raw={},
            ),
        ]
    )
    session.commit()

    dataset = build_outcome_dataset(session)

    assert dataset.eligible_count == 1
    assert dataset.complete_count == 0
    assert dataset.rows[0].observation_at is None
    assert dataset.rows[0].post_scan_at is None


def _lineage_fixture(
    session: Session,
) -> tuple[Table, MaintenanceJob, MaintenanceIntent]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(
        id=uuid4(),
        name=f"destination-{uuid4()}",
        engine=EngineType.TRINO.value,
        config={},
    )
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=f"events_{uuid4().hex}",
        is_active=True,
    )
    run = MaintenanceRun(
        id=uuid4(),
        kind="adhoc",
        trigger="test",
        status=JobStatus.SUCCEEDED.value,
    )
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode=Mode.STANDARD.value,
        status=JobStatus.SUCCEEDED.value,
        plan={"table_ref": "iceberg.analytics.events", "operations": [{"type": "optimize"}]},
        plan_hash="a" * 64,
    )
    intent = MaintenanceIntent(
        id=uuid4(),
        job_id=job.id,
        version=1,
        status="succeeded",
        actor_type="test",
        actor_id="operator",
        source_id=source.id,
        table_id=table.id,
        table_ref="iceberg.analytics.events",
        destination_id=destination.id,
        operation_scope=["optimize"],
        canonical_plan=job.plan,
        plan_hash=job.plan_hash,
        idempotency_key=f"lineage:{job.id}",
        approval_bindings=[],
        fencing_epoch=1,
        budget={},
        capability_requirements=[],
        containment_action="reconcile",
        expires_at=datetime(2026, 8, 5, tzinfo=UTC),
    )
    session.add_all([source, destination, table, run, job])
    session.flush()
    session.add(intent)
    session.flush()
    job.current_intent_id = intent.id
    session.commit()
    return table, job, intent
