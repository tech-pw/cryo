"""Tests for cryo.ml.interval_model -- calibrated runtime/rewrite-byte prediction intervals (I4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
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
    TableStatsHistory,
)
from cryo.domain.enums import EngineType, JobStatus, Mode
from cryo.intelligence.outcomes import build_outcome_dataset
from cryo.ml.interval_model import (
    REWRITE_BYTES,
    RUNTIME_MS,
    evaluate_interval_model,
    fit_interval_model,
    predict_interval,
)


def _rewrite_rows(
    n: int,
    *,
    ratio: float = 0.8,
    engine: str = "trino",
    action: str = "optimize",
    predicted_start: float = 1_000.0,
) -> list[dict[str, object]]:
    return [
        {
            "action": action,
            "engine": engine,
            "estimated_rewrite_bytes": predicted_start + index * 100.0,
            "actual_rewrite_bytes": (predicted_start + index * 100.0) * ratio,
        }
        for index in range(n)
    ]


def _runtime_rows(
    n: int,
    *,
    ms_per_file: float = 25.0,
    engine: str = "trino",
    action: str = "optimize",
    files_start: float = 100.0,
) -> list[dict[str, object]]:
    return [
        {
            "action": action,
            "engine": engine,
            "input_files_count": files_start + index * 10.0,
            "duration_ms": (files_start + index * 10.0) * ms_per_file,
        }
        for index in range(n)
    ]


def test_fit_interval_model_abstains_below_minimum_group_size() -> None:
    rows = _rewrite_rows(5)
    model = fit_interval_model(rows, REWRITE_BYTES)

    assert model.groups == {}
    prediction = predict_interval({"action": "optimize", "engine": "trino"}, model)
    assert prediction.used_model is False
    assert prediction.abstention_reason == "insufficient_evidence"


def test_rewrite_bytes_interval_predicts_engine_specific_scope() -> None:
    rows = _rewrite_rows(20, ratio=0.8)
    model = fit_interval_model(rows, REWRITE_BYTES, min_group_rows=12)

    prediction = predict_interval(
        {"action": "optimize", "engine": "trino", "estimated_rewrite_bytes": 1_500.0},
        model,
    )

    assert prediction.used_model is True
    assert prediction.scope == "engine"
    assert prediction.point_estimate == 1_200.0  # 1500 * 0.8
    assert prediction.lower <= prediction.point_estimate <= prediction.upper
    assert prediction.abstention_reason is None


def test_rewrite_bytes_interval_falls_back_to_pooled_scope_when_engine_sample_is_small() -> None:
    trino_rows = _rewrite_rows(5, engine="trino")
    spark_rows = _rewrite_rows(15, engine="spark_emr")
    model = fit_interval_model(trino_rows + spark_rows, REWRITE_BYTES, min_group_rows=12)

    # Trino has only 5 rows (below the minimum); the pooled cross-engine group must be used and
    # the prediction must be honest that it is not engine-specific.
    prediction = predict_interval(
        {"action": "optimize", "engine": "trino", "estimated_rewrite_bytes": 1_100.0},
        model,
    )

    assert prediction.used_model is True
    assert prediction.scope == "pooled"


def test_interval_abstains_outside_training_domain() -> None:
    rows = _rewrite_rows(20)
    model = fit_interval_model(rows, REWRITE_BYTES, min_group_rows=12)

    prediction = predict_interval(
        {"action": "optimize", "engine": "trino", "estimated_rewrite_bytes": 1_000_000.0},
        model,
    )

    assert prediction.used_model is False
    assert prediction.abstention_reason == "outside_training_domain"
    assert prediction.point_estimate == 0.0


def test_interval_abstains_on_missing_reference_feature() -> None:
    rows = _rewrite_rows(20)
    model = fit_interval_model(rows, REWRITE_BYTES, min_group_rows=12)

    prediction = predict_interval({"action": "optimize", "engine": "trino"}, model)

    assert prediction.used_model is False
    assert prediction.abstention_reason == "missing_feature"


def test_interval_abstains_for_unknown_action() -> None:
    rows = _rewrite_rows(20)
    model = fit_interval_model(rows, REWRITE_BYTES, min_group_rows=12)

    prediction = predict_interval(
        {"action": "remove_orphan_files", "engine": "trino", "estimated_rewrite_bytes": 1_000.0},
        model,
    )

    assert prediction.used_model is False
    assert prediction.abstention_reason == "insufficient_evidence"


def test_runtime_interval_uses_input_files_count_as_feature() -> None:
    rows = _runtime_rows(20, ms_per_file=25.0)
    model = fit_interval_model(rows, RUNTIME_MS, min_group_rows=12)

    prediction = predict_interval(
        {"action": "optimize", "engine": "trino", "input_files_count": 150.0}, model
    )

    assert prediction.used_model is True
    assert prediction.point_estimate == 3_750.0  # 150 * 25


def test_chronological_fit_uses_only_leading_rows_not_the_full_series() -> None:
    """Split-conformal calibration must not let late-arriving rows change the fitted ratio --
    only the leading (earlier-in-time) slice may set the point estimate, per the module's
    leakage-prevention contract."""
    early_ratio_rows = _rewrite_rows(14, ratio=0.5)
    late_ratio_rows = _rewrite_rows(6, ratio=0.5, predicted_start=2_000.0)
    # A drastic ratio shift placed at the very end (within the calibration tail) must widen the
    # band rather than move the point estimate, which is fit only from the leading rows.
    shocked_tail = [
        {
            **row,
            "actual_rewrite_bytes": row["estimated_rewrite_bytes"] * 5.0,
        }
        for row in late_ratio_rows
    ]

    model = fit_interval_model(early_ratio_rows + shocked_tail, REWRITE_BYTES, min_group_rows=12)
    prediction = predict_interval(
        {"action": "optimize", "engine": "trino", "estimated_rewrite_bytes": 1_000.0},
        model,
        min_confidence=0.0,
    )

    assert prediction.used_model is True
    # Point estimate still reflects the leading 0.5 ratio, not the shocked tail's 5.0 ratio.
    assert prediction.point_estimate == 500.0
    # But the band widened enough to (partially) absorb the shocked residual quantile.
    assert prediction.upper > prediction.point_estimate


def test_evaluate_interval_model_reports_mape_and_coverage_with_abstentions_separated() -> None:
    training = _rewrite_rows(20, ratio=0.8)
    model = fit_interval_model(training, REWRITE_BYTES, min_group_rows=12)

    holdout = _rewrite_rows(5, ratio=0.8, predicted_start=1_500.0) + [
        {
            "action": "optimize",
            "engine": "trino",
            "estimated_rewrite_bytes": 10_000_000.0,
            "actual_rewrite_bytes": 8_000_000.0,
        }
    ]

    evaluation = evaluate_interval_model(holdout, model)

    assert evaluation.n_samples == 6
    assert evaluation.abstentions == 1  # the out-of-domain row
    assert evaluation.n_evaluated == 5
    assert evaluation.mape_median is not None
    assert evaluation.mape_median < 0.05
    assert evaluation.interval_coverage is not None
    assert len(evaluation.by_group) == 1
    group = evaluation.by_group[0]
    assert group.engine == "trino"
    assert group.action == "optimize"
    assert group.scope == "engine"
    assert group.n_samples == 6
    assert group.n_evaluated == 5


def test_evaluate_interval_model_scores_zero_samples_without_dividing_by_zero() -> None:
    model = fit_interval_model(_rewrite_rows(20), REWRITE_BYTES, min_group_rows=12)
    evaluation = evaluate_interval_model([], model)

    assert evaluation.n_samples == 0
    assert evaluation.abstention_rate == 0.0
    assert evaluation.mape_median is None
    assert evaluation.interval_coverage is None


def test_fit_interval_model_rejects_invalid_target_coverage() -> None:
    with pytest.raises(ValueError):
        fit_interval_model(_rewrite_rows(20), REWRITE_BYTES, target_coverage=1.0)


def test_outcome_lineage_feeds_interval_model_end_to_end(session: Session) -> None:
    """The interval model must consume OutcomeDataset.calibration_rows() directly -- the I1/I4
    integration this initiative is grounded in."""
    base = datetime(2026, 8, 1, tzinfo=UTC)
    for i in range(16):
        table, job, intent = _lineage_fixture(session)
        event_at = base + timedelta(hours=i)
        attempt = MaintenanceAttempt(
            id=uuid4(),
            intent_id=intent.id,
            attempt_number=1,
            worker_id=f"worker-{i}",
            fencing_epoch=1,
            status="succeeded",
            started_at=event_at,
            heartbeat_at=event_at,
            completed_at=event_at + timedelta(seconds=2),
        )
        effect = EffectReceipt(
            id=uuid4(),
            intent_id=intent.id,
            attempt_id=attempt.id,
            destination_id=intent.destination_id,
            step_index=0,
            operation_type="optimize",
            strategy="stable_reference_probe",
            state="succeeded",
            idempotency_key=f"effect:{intent.id}",
            result={"actual_bytes_written": int(5.0 * 1024**3 * 0.8)},
            submitted_at=event_at,
            resolved_at=event_at + timedelta(seconds=2),
        )
        session.add_all(
            [
                attempt,
                effect,
                TableStatsHistory(
                    table_id=table.id,
                    scan_at=event_at - timedelta(minutes=30),
                    small_files_count=50,
                    total_files=60,
                    total_size_gb=5.0,
                    raw={"reclaim_optimize_bytes": 1_000 + i * 50},
                ),
                AuditLog(
                    ts=event_at,
                    table_id=table.id,
                    job_id=job.id,
                    action="optimize",
                    status="OK",
                    engine=EngineType.TRINO.value,
                    actual_bytes_freed=int((1_000 + i * 50) * 0.8),
                    duration_ms=(1_000 + i * 50) * 2,
                    input_files_count=40,
                    output_files_count=4,
                ),
                TableStatsHistory(
                    table_id=table.id,
                    scan_at=event_at + timedelta(minutes=30),
                    small_files_count=4,
                    total_files=10,
                    total_size_gb=4.9,
                    raw={},
                ),
            ]
        )
    session.commit()

    dataset = build_outcome_dataset(session, limit=100)
    calibration_rows = dataset.calibration_rows()
    assert len(calibration_rows) == 16
    assert all(row["duration_ms"] is not None for row in calibration_rows)

    bytes_model = fit_interval_model(calibration_rows, REWRITE_BYTES, min_group_rows=12)
    runtime_model = fit_interval_model(calibration_rows, RUNTIME_MS, min_group_rows=12)

    bytes_prediction = predict_interval(
        {
            "action": "optimize",
            "engine": "trino",
            "estimated_rewrite_bytes": int(5.0 * 1024**3),
        },
        bytes_model,
    )
    runtime_prediction = predict_interval(
        {"action": "optimize", "engine": "trino", "input_files_count": 40.0}, runtime_model
    )

    assert bytes_prediction.used_model is True
    assert bytes_prediction.scope == "engine"
    assert runtime_prediction.used_model is True
    assert runtime_prediction.scope == "engine"


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
