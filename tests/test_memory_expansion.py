"""Per-table memory expansion factor estimation.

Context: docs/native-go-compaction-throughput.md's tuned-par2-48b row shows the fleet-wide
constant 96 forcing serial execution at RSS well below budget -- worse wall time for no memory
benefit. These tests pin the safety properties that make a per-table estimate acceptable.
"""

from __future__ import annotations

import pytest

from cryo.engines.native.memory_expansion import (
    DEFAULT_MEMORY_EXPANSION_FACTOR,
    MAX_MEMORY_EXPANSION_FACTOR,
    MIN_MEMORY_EXPANSION_FACTOR,
    estimate_memory_expansion_factor,
)

_GB = 1024**3


def test_missing_inputs_fall_back_to_the_pessimistic_default() -> None:
    for kwargs in (
        {"total_size_bytes": None, "total_records": 1_000, "column_count": 10},
        {"total_size_bytes": _GB, "total_records": None, "column_count": 10},
        {"total_size_bytes": _GB, "total_records": 1_000, "column_count": None},
        {"total_size_bytes": 0, "total_records": 1_000, "column_count": 10},
        {"total_size_bytes": _GB, "total_records": 0, "column_count": 10},
    ):
        estimate = estimate_memory_expansion_factor(**kwargs)  # type: ignore[arg-type]
        assert estimate.factor == DEFAULT_MEMORY_EXPANSION_FACTOR
        assert estimate.measured is False


def test_estimate_never_exceeds_the_current_fleet_default() -> None:
    """This may only recover throughput pessimism, never admit more memory than today.

    A pathologically wide, highly-compressed table would model above 96; it must clamp.
    """
    estimate = estimate_memory_expansion_factor(
        total_size_bytes=1024 * 1024,  # 1 MiB compressed
        total_records=50_000_000,
        column_count=200,
    )
    assert estimate.factor == MAX_MEMORY_EXPANSION_FACTOR == DEFAULT_MEMORY_EXPANSION_FACTOR


def test_estimate_never_drops_below_the_arrow_overhead_floor() -> None:
    """Incompressible data still costs Arrow buffers, offsets, and validity bitmaps."""
    estimate = estimate_memory_expansion_factor(
        total_size_bytes=500 * _GB,  # barely compressed
        total_records=1_000_000,
        column_count=2,
    )
    assert estimate.factor == MIN_MEMORY_EXPANSION_FACTOR
    assert estimate.measured is True


def test_narrow_poorly_compressing_table_estimates_below_the_default() -> None:
    """The case that unlocks concurrency: real RSS is far under what 96 assumes."""
    estimate = estimate_memory_expansion_factor(
        total_size_bytes=100 * _GB, total_records=200_000_000, column_count=5
    )
    assert estimate.factor < DEFAULT_MEMORY_EXPANSION_FACTOR
    assert estimate.measured is True
    assert "estimated from" in estimate.reason


def test_wide_dense_table_stays_conservative() -> None:
    """The 29-column fixture shape the original constant was derived from."""
    estimate = estimate_memory_expansion_factor(
        total_size_bytes=10 * _GB, total_records=500_000_000, column_count=29
    )
    assert estimate.factor > MIN_MEMORY_EXPANSION_FACTOR
    assert estimate.factor <= DEFAULT_MEMORY_EXPANSION_FACTOR


def test_expansion_falls_as_compression_worsens() -> None:
    """Monotonicity: the same rows in more compressed bytes must need less expansion."""
    factors = [
        estimate_memory_expansion_factor(
            total_size_bytes=size, total_records=100_000_000, column_count=20
        ).factor
        for size in (5 * _GB, 20 * _GB, 80 * _GB)
    ]
    assert factors == sorted(factors, reverse=True)


@pytest.mark.parametrize("columns", [1, 5, 29, 200])
def test_estimate_always_lands_inside_the_clamps(columns: int) -> None:
    estimate = estimate_memory_expansion_factor(
        total_size_bytes=42 * _GB, total_records=123_456_789, column_count=columns
    )
    assert MIN_MEMORY_EXPANSION_FACTOR <= estimate.factor <= MAX_MEMORY_EXPANSION_FACTOR
