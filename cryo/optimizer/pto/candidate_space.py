from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from cryo.optimizer.partition_analyzer import cardinality_hint

TARGET_FILE_SIZES_MB = (128, 256, 512)
ROW_GROUP_SIZES_MB = (16, 32, 64, 128)


@dataclass(frozen=True, slots=True)
class PtoPattern:
    column_name: str
    filter_count: int
    equality_filter_count: int
    range_filter_count: int
    avg_selectivity: float | None

    @property
    def pruning_value(self) -> float:
        raw_selectivity = self.avg_selectivity if self.avg_selectivity is not None else 0.5
        selectivity = min(1.0, max(0.0, raw_selectivity))
        return self.filter_count * (1.0 - selectivity)


@dataclass(frozen=True, slots=True)
class CandidateTuple:
    partition_spec: str | None
    partition_column: str | None
    target_file_size_mb: int
    row_group_size_mb: int
    strategy: str
    sort_columns: tuple[str, ...]
    estimated_file_count: int
    estimated_rewrite_gb: float


def build_candidate_space(
    patterns: Sequence[PtoPattern],
    *,
    total_size_gb: float,
    total_files: int,
    candidate_cap: int = 64,
) -> list[CandidateTuple]:
    if candidate_cap < 1:
        raise ValueError("PTO candidate cap must be positive")
    ranked = sorted(patterns, key=lambda pattern: (-pattern.pruning_value, pattern.column_name))
    partition_options: list[tuple[str | None, str | None]] = [(None, None)]
    for pattern in ranked:
        cardinality = cardinality_hint(pattern.column_name)
        if cardinality == "low":
            partition_options.append((f"identity({pattern.column_name})", pattern.column_name))
        elif cardinality == "high" and pattern.equality_filter_count > 0:
            bucket_count = _bucket_count(total_size_gb)
            partition_options.append(
                (f"bucket[{bucket_count}]({pattern.column_name})", pattern.column_name)
            )
        if len(partition_options) >= 4:
            break

    candidates: list[CandidateTuple] = []
    for partition_spec, partition_column in partition_options:
        remaining = [
            pattern.column_name for pattern in ranked if pattern.column_name != partition_column
        ]
        strategies = [
            ("binpack", ()),
            ("sort", tuple(remaining[:2])),
        ]
        if len(remaining) >= 2:
            strategies.append(("zorder", tuple(remaining[:3])))
        for strategy, sort_columns in strategies:
            if strategy != "binpack" and not sort_columns:
                continue
            for target_mb in TARGET_FILE_SIZES_MB:
                expected_files = max(1, math.ceil(total_size_gb * 1024 / target_mb))
                for row_group_mb in ROW_GROUP_SIZES_MB:
                    if row_group_mb > target_mb:
                        continue
                    candidates.append(
                        CandidateTuple(
                            partition_spec=partition_spec,
                            partition_column=partition_column,
                            target_file_size_mb=target_mb,
                            row_group_size_mb=row_group_mb,
                            strategy=strategy,
                            sort_columns=sort_columns,
                            estimated_file_count=min(max(total_files, 1), expected_files),
                            estimated_rewrite_gb=max(0.0, total_size_gb),
                        )
                    )
    return candidates


def _bucket_count(total_size_gb: float) -> int:
    desired = max(4, math.ceil(max(total_size_gb, 1.0) / 0.5))
    power = 1
    while power < desired:
        power *= 2
    return min(power, 2048)
