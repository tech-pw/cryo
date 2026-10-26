"""Move 9 — metadata-to-data byte ratio as an explicit health signal.

The helper in ``cryo/scanner/signals.py`` is already wired through ``enrich_stats_signals``
into ``compute_health_score``. These tests pin the contract so it cannot regress silently.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cryo.scanner.health import compute_health_score
from cryo.scanner.signals import metadata_to_data_ratio


def test_metadata_to_data_ratio_helper() -> None:
    assert metadata_to_data_ratio(50, 1000) == 0.05
    assert metadata_to_data_ratio(0, 0) == 0.0
    assert metadata_to_data_ratio(150, 1000) == 0.15


def test_metadata_to_data_ratio_zero_data_bytes_is_worst_case() -> None:
    # All metadata, no live data: worst case, not perfect (issue #191).
    assert metadata_to_data_ratio(1_000_000, 0) == 1.0
    assert metadata_to_data_ratio(0, 0) == 0.0


def test_health_score_deducts_warn_metadata_ratio() -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    stats = {
        "total_files": 100,
        "delete_file_pct": 0,
        "total_delete_files": 0,
        "total_snapshots": 0,
        "first_updated_at": now,
        "manifest_count": 0,
        "total_partitions": 0,
        "partition_skew_ratio": 0,
        "avg_files_per_partition": 0,
        "write_distribution_mode": "hash",
        "has_sort_order": True,
        "target_file_size_bytes": 268_435_456,
        "file_formats": ["PARQUET"],
        "metadata_to_data_ratio": 0.08,
    }

    score, _grade, deductions = compute_health_score(stats, health_small_file_count=0, now=now)

    metadata_deds = [d for d in deductions if d["label"] == "Metadata ratio overhead"]
    assert metadata_deds, "expected a metadata-ratio health deduction"
    assert metadata_deds[0]["severity"] == "warning"
    assert metadata_deds[0]["deduction"] == 8
    assert "8.0%" in metadata_deds[0]["reason"]
    assert score == 92


def test_health_score_deducts_crit_metadata_ratio() -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    stats = {
        "total_files": 100,
        "delete_file_pct": 0,
        "total_delete_files": 0,
        "total_snapshots": 0,
        "first_updated_at": now,
        "manifest_count": 0,
        "total_partitions": 0,
        "partition_skew_ratio": 0,
        "avg_files_per_partition": 0,
        "write_distribution_mode": "hash",
        "has_sort_order": True,
        "target_file_size_bytes": 268_435_456,
        "file_formats": ["PARQUET"],
        "metadata_to_data_ratio": 0.20,
    }

    _score, _grade, deductions = compute_health_score(stats, health_small_file_count=0, now=now)

    metadata_deds = [d for d in deductions if d["label"] == "Metadata ratio overhead"]
    assert metadata_deds, "expected a metadata-ratio health deduction"
    assert metadata_deds[0]["severity"] == "critical"
    assert metadata_deds[0]["deduction"] == 20
