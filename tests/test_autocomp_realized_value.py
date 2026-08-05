from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from cryo.intelligence.autocomp_feedback import (
    AUTOCOMP_FEEDBACK_SCHEMA_VERSION,
    build_autocomp_realized_value,
    realized_value_multiplier,
)
from cryo.intelligence.outcomes import MaintenanceOutcome, OutcomeDataset


def test_realized_value_qualifies_stable_history_and_bounds_multiplier() -> None:
    base = _outcome(predicted=1000, actual=800, rewrite=4000)
    dataset = OutcomeDataset(
        tuple(
            replace(
                base,
                audit_id=index,
                actual_bytes_freed=800 + (index % 2) * 20,
            )
            for index in range(20)
        )
    )

    feedback = build_autocomp_realized_value(dataset)

    assert feedback.schema == AUTOCOMP_FEEDBACK_SCHEMA_VERSION
    assert feedback.status == "qualified"
    assert feedback.evidence_sufficient is True
    assert feedback.stable is True
    assert feedback.calibration_multiplier == 0.81
    assert feedback.realized_reclaim_per_rewrite_byte == 0.2025
    assert realized_value_multiplier({"autocomp_realized_value": feedback.to_dict()}) == 0.81


def test_realized_value_abstains_on_sparse_evidence_without_changing_scores() -> None:
    dataset = OutcomeDataset(
        tuple(
            replace(_outcome(predicted=1000, actual=100, rewrite=4000), audit_id=index)
            for index in range(5)
        )
    )

    feedback = build_autocomp_realized_value(dataset)

    assert feedback.status == "abstained"
    assert feedback.abstention_reason == "insufficient_outcome_evidence"
    assert feedback.calibration_multiplier == 1.0
    assert realized_value_multiplier({"autocomp_realized_value": feedback.to_dict()}) == 1.0


def test_realized_value_abstains_when_recent_outcomes_drift() -> None:
    earlier = [
        replace(_outcome(predicted=1000, actual=1000, rewrite=4000), audit_id=index)
        for index in range(10)
    ]
    recent = [
        replace(_outcome(predicted=1000, actual=200, rewrite=4000), audit_id=10 + index)
        for index in range(10)
    ]

    feedback = build_autocomp_realized_value(OutcomeDataset(tuple((*earlier, *recent))))

    assert feedback.stable is False
    assert feedback.abstention_reason == "realized_value_drift"
    assert feedback.calibration_multiplier == 1.0


def test_multiplier_rejects_unqualified_or_malformed_feedback() -> None:
    assert realized_value_multiplier(None) == 1.0
    assert (
        realized_value_multiplier(
            {
                "autocomp_realized_value": {
                    "schema": AUTOCOMP_FEEDBACK_SCHEMA_VERSION,
                    "status": "qualified",
                    "evidence_sufficient": True,
                    "stable": True,
                    "calibration_multiplier": 99,
                }
            }
        )
        == 1.5
    )
    assert (
        realized_value_multiplier(
            {
                "autocomp_realized_value": {
                    "schema": "unknown",
                    "status": "qualified",
                    "evidence_sufficient": True,
                    "stable": True,
                    "calibration_multiplier": 0.5,
                }
            }
        )
        == 1.0
    )


def _outcome(
    *,
    predicted: int,
    actual: int,
    rewrite: int,
) -> MaintenanceOutcome:
    now = datetime(2026, 8, 5, tzinfo=UTC)
    return MaintenanceOutcome(
        audit_id=1,
        job_id=uuid4(),
        intent_id=uuid4(),
        plan_hash="a" * 64,
        table_id=uuid4(),
        action="optimize",
        engine="trino",
        outcome_at=now,
        observation_at=now,
        post_scan_at=now,
        predicted_bytes_freed=predicted,
        predicted_savings_usd_month=1.0,
        actual_bytes_freed=actual,
        estimated_rewrite_bytes=rewrite,
        actual_rewrite_bytes=rewrite,
        duration_ms=1000,
        input_files_count=100,
        pre_small_files_count=100,
        post_small_files_count=10,
        pre_total_files=120,
        post_total_files=30,
        pre_total_size_gb=1.0,
        post_total_size_gb=0.9,
        pre_delete_file_pct=0.0,
        post_delete_file_pct=0.0,
        pre_p95_query_latency_ms=100.0,
        post_p95_query_latency_ms=90.0,
    )
