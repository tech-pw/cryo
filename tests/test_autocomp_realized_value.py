from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from cryo.intelligence.autocomp_feedback import (
    AUTOCOMP_FEEDBACK_SCHEMA_VERSION,
    build_autocomp_realized_value,
    realized_cost_multiplier,
    realized_latency_multiplier,
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


def test_cost_and_latency_calibration_qualify_and_reward_real_reductions() -> None:
    """The actual point of this feature: a table whose real Trino cost and p95 latency both
    demonstrably dropped after compaction should qualify a >1.0 multiplier on each -- distinct
    from and independent of the byte-yield multiplier, which this fixture also keeps stable so
    both signals can be checked without one masking the other."""
    base = _outcome(
        predicted=1000,
        actual=1000,
        rewrite=4000,
        pre_cost_usd=10.0,
        post_cost_usd=7.0,  # 30% real cost reduction
        pre_latency_ms=200.0,
        post_latency_ms=150.0,  # 25% real latency reduction
    )
    dataset = OutcomeDataset(tuple(replace(base, audit_id=index) for index in range(20)))

    feedback = build_autocomp_realized_value(dataset)

    assert feedback.cost_status == "qualified"
    assert feedback.cost_evidence_sufficient is True
    assert feedback.cost_stable is True
    assert feedback.realized_cost_ratio == 0.7
    assert feedback.cost_calibration_multiplier == 1.3  # 2.0 - 0.7, real cost win rewarded
    assert realized_cost_multiplier({"autocomp_realized_value": feedback.to_dict()}) == 1.3

    assert feedback.latency_status == "qualified"
    assert feedback.realized_latency_ratio == 0.75
    assert feedback.latency_calibration_multiplier == 1.25
    assert realized_latency_multiplier({"autocomp_realized_value": feedback.to_dict()}) == 1.25


def test_cost_calibration_penalizes_real_cost_increase() -> None:
    """The inverse case: compaction ran, bytes freed as predicted, but real Trino cost went
    *up* afterward -- the cost multiplier must drop below 1.0, not stay neutral, since this is
    exactly the signal meant to catch "predicted savings didn't materialize as real cost"."""
    base = _outcome(
        predicted=1000,
        actual=1000,
        rewrite=4000,
        pre_cost_usd=10.0,
        post_cost_usd=13.0,  # 30% real cost increase
    )
    dataset = OutcomeDataset(tuple(replace(base, audit_id=index) for index in range(20)))

    feedback = build_autocomp_realized_value(dataset)

    assert feedback.cost_status == "qualified"
    assert feedback.realized_cost_ratio == 1.3
    assert feedback.cost_calibration_multiplier == 0.7  # 2.0 - 1.3, real regression penalized


def test_cost_calibration_abstains_without_enough_cost_data() -> None:
    """Most outcomes have no cost data yet (e.g. trino_cost_pricing_enabled was off for most
    of this history) -- must abstain (neutral 1.0) rather than qualify on a handful of pairs,
    independently of whether the byte-yield calibration itself qualifies."""
    with_cost = [
        replace(
            _outcome(predicted=1000, actual=1000, rewrite=4000, post_cost_usd=8.0),
            audit_id=index,
        )
        for index in range(3)
    ]
    without_cost = [
        replace(
            _outcome(
                predicted=1000,
                actual=1000,
                rewrite=4000,
                pre_cost_usd=None,
                post_cost_usd=None,
            ),
            audit_id=3 + index,
        )
        for index in range(17)
    ]
    dataset = OutcomeDataset(tuple((*with_cost, *without_cost)))

    feedback = build_autocomp_realized_value(dataset)

    # Byte-yield calibration qualifies fine (20 obs, all identical actual/predicted).
    assert feedback.status == "qualified"
    # Cost calibration abstains -- only 3 of 20 rows have real cost data.
    assert feedback.cost_status == "abstained"
    assert feedback.cost_abstention_reason == "insufficient_outcome_evidence"
    assert feedback.cost_calibration_multiplier == 1.0
    assert realized_cost_multiplier({"autocomp_realized_value": feedback.to_dict()}) == 1.0


def test_cost_and_latency_calibration_are_independent_of_byte_yield_calibration() -> None:
    """A table can have byte predictions that are wildly off (triggering byte-yield drift
    abstention) while its real cost/latency outcome is perfectly stable -- the two must not be
    coupled through a shared abstention reason or drift check."""
    earlier = [
        replace(
            _outcome(
                predicted=1000,
                actual=1000,
                rewrite=4000,
                pre_cost_usd=10.0,
                post_cost_usd=8.0,
            ),
            audit_id=index,
        )
        for index in range(10)
    ]
    recent = [
        replace(
            _outcome(
                predicted=1000,
                actual=200,  # byte-yield drifts hard
                rewrite=4000,
                pre_cost_usd=10.0,
                post_cost_usd=8.0,  # cost outcome stays identical
            ),
            audit_id=10 + index,
        )
        for index in range(10)
    ]

    feedback = build_autocomp_realized_value(OutcomeDataset(tuple((*earlier, *recent))))

    assert feedback.stable is False  # byte-yield calibration correctly abstains
    assert feedback.calibration_multiplier == 1.0
    assert feedback.cost_stable is True  # cost calibration is unaffected
    assert feedback.cost_status == "qualified"
    assert feedback.cost_calibration_multiplier == 1.2  # 2.0 - 0.8


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
    pre_cost_usd: float | None = 10.0,
    post_cost_usd: float | None = 9.0,
    pre_latency_ms: float | None = 100.0,
    post_latency_ms: float | None = 90.0,
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
        pre_p95_query_latency_ms=pre_latency_ms,
        post_p95_query_latency_ms=post_latency_ms,
        pre_estimated_cost_usd=pre_cost_usd,
        post_estimated_cost_usd=post_cost_usd,
    )
