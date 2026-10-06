from __future__ import annotations

from cryo.scanner.analyze import (
    RAW_PARTITION_PREVIEW_LIMIT,
    PartitionAnalysis,
    raw_partition_preview,
)


def _partition(key: str, small_files: int, size_gb: float = 1.0) -> PartitionAnalysis:
    return PartitionAnalysis(
        partition_value=key,
        partition_values={"day": key},
        partition_fields=[],
        partition_spec_id=0,
        partition_path=None,
        partition_key=key,
        base_column="day",
        base_column_type="date",
        transform="day",
        files_per_partition=small_files,
        small_files_in_partition_count=small_files,
        partition_size_gb=size_gb,
        compaction_status="needs_compaction",
        raw={},
    )


def test_raw_partition_preview_keeps_only_the_worst_partitions() -> None:
    partitions = [_partition(f"p{i:04d}", small_files=i) for i in range(5000)]

    preview = raw_partition_preview(partitions)

    assert len(preview) == RAW_PARTITION_PREVIEW_LIMIT
    assert preview[0]["partition_key"] == "p4999"
    assert [row["small_files_in_partition_count"] for row in preview] == sorted(
        (row["small_files_in_partition_count"] for row in preview), reverse=True
    )


def test_raw_partition_preview_breaks_ties_deterministically() -> None:
    partitions = [_partition("b", 3, 2.0), _partition("a", 3, 2.0), _partition("c", 3, 5.0)]

    assert [row["partition_key"] for row in raw_partition_preview(partitions)] == ["c", "a", "b"]
