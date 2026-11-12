"""Fragmented partitions grades excess files, not raw file count (#518)."""

from __future__ import annotations

from typing import Any

from cryo.scanner.health import (
    HEALTH_THRESHOLDS,
    compute_health_score,
    excess_files_per_partition,
)

MIB = 1024 * 1024


def _labels(stats: dict[str, Any]) -> dict[str, int]:
    _, _, deductions = compute_health_score(stats, 0)
    return {d["label"]: d["deduction"] for d in deductions}


def _table(files: int, file_mb: float, partitions: int = 1, **extra: Any) -> dict[str, Any]:
    return {
        "total_files": files,
        "total_partitions": partitions,
        "avg_files_per_partition": files / partitions,
        "avg_file_size_mb": file_mb,
        "total_size_gb": files * file_mb / 1024,
        **extra,
    }


def test_large_well_compacted_unpartitioned_table_has_no_deduction() -> None:
    stats = _table(448, 345.0)
    assert excess_files_per_partition(stats) == 0
    assert "Fragmented partitions" not in _labels(stats)


def test_files_at_table_target_size_have_no_deduction() -> None:
    stats = _table(448, 512.0, target_file_size_bytes=512 * MIB)
    assert "Fragmented partitions" not in _labels(stats)


def test_partition_of_tiny_files_is_fragmented() -> None:
    stats = _table(448, 2.0)
    assert excess_files_per_partition(stats) == 443  # 896 MiB needs ceil(896 / 192) = 5 files
    assert _labels(stats)["Fragmented partitions"] == 20


def test_files_between_min_size_and_target_are_not_excess() -> None:
    # 200 MiB files are above 75% of a 256 MiB target: rewrite_data_files would skip them.
    assert excess_files_per_partition(_table(448, 200.0)) == 0


def test_weights_and_thresholds_unchanged() -> None:
    assert HEALTH_THRESHOLDS["avg_files_per_partition_warn"] == 50
    assert HEALTH_THRESHOLDS["avg_files_per_partition_crit"] == 200
    assert _labels(_table(60, 1.0))["Fragmented partitions"] == 8
