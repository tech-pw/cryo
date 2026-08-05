from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from cryo.intelligence.delete_lifecycle_evidence import (
    DELETE_LIFECYCLE_EVIDENCE_SCHEMA,
    evaluate_delete_lifecycle,
)
from cryo.intelligence.outcomes import MaintenanceOutcome, OutcomeDataset


def test_delete_lifecycle_reports_matched_advisory_value_without_causal_claim() -> None:
    lifecycle = [
        replace(
            _outcome("rewrite_position_deletes", pre=12.0, post=2.0, rewrite_gib=1),
            audit_id=index,
        )
        for index in range(10)
    ]
    threshold = [
        replace(
            _outcome("convert_deletes", pre=12.0, post=4.0, rewrite_gib=4),
            audit_id=10 + index,
        )
        for index in range(10)
    ]

    evidence = evaluate_delete_lifecycle(OutcomeDataset(tuple((*lifecycle, *threshold))))

    assert evidence.schema == DELETE_LIFECYCLE_EVIDENCE_SCHEMA
    assert evidence.status == "advisory"
    assert evidence.qualifies_policy_gate is False
    assert evidence.matched_engines == ("spark_emr",)
    assert evidence.lifecycle.median_value_per_rewrite_gib == 10.0
    assert evidence.threshold_only.median_value_per_rewrite_gib == 2.0
    assert evidence.observed_value_lift == 8.0
    assert "historical_cohorts_are_not_randomized_counterfactuals" in evidence.blockers


def test_delete_lifecycle_abstains_for_missing_or_unmeasured_cohorts() -> None:
    incomplete = replace(
        _outcome("rewrite_position_deletes", pre=10.0, post=2.0, rewrite_gib=1),
        actual_rewrite_bytes=None,
    )

    evidence = evaluate_delete_lifecycle(OutcomeDataset((incomplete,)))

    assert evidence.status == "insufficient_evidence"
    assert evidence.lifecycle.observations == 0
    assert evidence.threshold_only.observations == 0
    assert "insufficient_lifecycle_observations" in evidence.blockers
    assert "insufficient_threshold_observations" in evidence.blockers
    assert "no_matched_engine_cohort" in evidence.blockers


def _outcome(
    action: str,
    *,
    pre: float,
    post: float,
    rewrite_gib: int,
) -> MaintenanceOutcome:
    now = datetime(2026, 8, 5, tzinfo=UTC)
    return MaintenanceOutcome(
        audit_id=1,
        job_id=uuid4(),
        intent_id=uuid4(),
        plan_hash="a" * 64,
        table_id=uuid4(),
        action=action,
        engine="spark_emr",
        outcome_at=now,
        observation_at=now,
        post_scan_at=now,
        predicted_bytes_freed=1000,
        predicted_savings_usd_month=1.0,
        actual_bytes_freed=800,
        estimated_rewrite_bytes=rewrite_gib * 1024**3,
        actual_rewrite_bytes=rewrite_gib * 1024**3,
        duration_ms=1000,
        input_files_count=100,
        pre_small_files_count=100,
        post_small_files_count=10,
        pre_total_files=120,
        post_total_files=30,
        pre_total_size_gb=10.0,
        post_total_size_gb=9.0,
        pre_delete_file_pct=pre,
        post_delete_file_pct=post,
        pre_p95_query_latency_ms=200.0,
        post_p95_query_latency_ms=150.0,
    )
