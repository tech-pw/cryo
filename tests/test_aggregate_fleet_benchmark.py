from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from cryo.intelligence.outcomes import MaintenanceOutcome, OutcomeDataset
from cryo.privacy.fleet_benchmark import (
    FLEET_BENCHMARK_SCHEMA,
    build_aggregate_fleet_benchmark,
)


def test_fleet_benchmark_publishes_only_k_anonymous_rounded_cohorts() -> None:
    rows = tuple(replace(_outcome(), table_id=uuid4(), audit_id=index) for index in range(20))

    report = build_aggregate_fleet_benchmark(OutcomeDataset(rows))

    assert report.schema_version == FLEET_BENCHMARK_SCHEMA
    assert report.contains_direct_identifiers is False
    assert report.differential_privacy is False
    assert report.suppressed_cohorts == 0
    assert len(report.published_cohorts) == 1
    cohort = report.published_cohorts[0]
    assert cohort.distinct_tables == 20
    assert cohort.median_runtime_ms == 1200
    assert cohort.median_rewrite_gib == 2.0
    payload = report.to_dict()
    schema = json.loads(
        (
            Path(__file__).parents[1] / "docs/schemas/aggregate-fleet-benchmark-v1.schema.json"
        ).read_text()
    )
    assert set(payload) == set(schema["required"])
    serialized = str(payload)
    assert "table_id" not in serialized
    assert "job_id" not in serialized
    assert "intent_id" not in serialized


def test_fleet_benchmark_suppresses_small_cohorts() -> None:
    rows = tuple(replace(_outcome(), table_id=uuid4(), audit_id=index) for index in range(9))

    report = build_aggregate_fleet_benchmark(
        OutcomeDataset(rows),
        min_distinct_tables=10,
        min_outcomes=10,
    )

    assert report.published_cohorts == ()
    assert report.suppressed_cohorts == 1


def _outcome() -> MaintenanceOutcome:
    now = datetime(2026, 8, 5, tzinfo=UTC)
    return MaintenanceOutcome(
        audit_id=1,
        job_id=uuid4(),
        intent_id=uuid4(),
        plan_hash="a" * 64,
        table_id=uuid4(),
        action="optimize",
        engine="native",
        outcome_at=now,
        observation_at=now,
        post_scan_at=now,
        predicted_bytes_freed=1024**3,
        predicted_savings_usd_month=5.0,
        actual_bytes_freed=1024**3,
        estimated_rewrite_bytes=2 * 1024**3,
        actual_rewrite_bytes=2 * 1024**3,
        duration_ms=1234,
        input_files_count=100,
        pre_small_files_count=100,
        post_small_files_count=10,
        pre_total_files=120,
        post_total_files=30,
        pre_total_size_gb=50.0,
        post_total_size_gb=49.0,
        pre_delete_file_pct=5.0,
        post_delete_file_pct=1.0,
        pre_p95_query_latency_ms=500.0,
        post_p95_query_latency_ms=400.0,
        pre_estimated_cost_usd=None,
        post_estimated_cost_usd=None,
    )
