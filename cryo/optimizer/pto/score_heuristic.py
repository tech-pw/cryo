from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cryo.optimizer.pto.candidate_space import CandidateTuple, PtoPattern


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    candidate: CandidateTuple
    estimated_cost: float
    estimated_scan_fraction: float
    estimated_file_open_ratio: float
    estimated_rewrite_ratio: float
    predicted_prune_improvement_pct: float


def score_candidates(
    candidates: Sequence[CandidateTuple],
    patterns: Sequence[PtoPattern],
    *,
    total_files: int,
    total_size_gb: float,
    candidate_cap: int = 64,
) -> list[ScoredCandidate]:
    values = {pattern.column_name: pattern.pruning_value for pattern in patterns}
    max_value = max(values.values(), default=1.0) or 1.0
    scored: list[ScoredCandidate] = []
    for candidate in candidates:
        partition_gain = 0.65 * values.get(candidate.partition_column or "", 0.0) / max_value
        sort_gain = min(
            0.30,
            0.18 * sum(values.get(column, 0.0) / max_value for column in candidate.sort_columns),
        )
        scan_fraction = max(0.02, 1.0 - partition_gain - sort_gain)
        file_open_ratio = candidate.estimated_file_count / max(total_files, 1)
        rewrite_ratio = candidate.estimated_rewrite_gb / max(total_size_gb, 0.001)
        estimated_cost = 0.60 * scan_fraction + 0.15 * file_open_ratio + 0.25 * rewrite_ratio
        scored.append(
            ScoredCandidate(
                candidate=candidate,
                estimated_cost=round(estimated_cost, 6),
                estimated_scan_fraction=round(scan_fraction, 6),
                estimated_file_open_ratio=round(file_open_ratio, 6),
                estimated_rewrite_ratio=round(rewrite_ratio, 6),
                predicted_prune_improvement_pct=round((1.0 - scan_fraction) * 100, 2),
            )
        )
    return sorted(
        scored,
        key=lambda item: (
            item.estimated_cost,
            item.candidate.partition_spec or "",
            item.candidate.strategy,
            item.candidate.target_file_size_mb,
            item.candidate.row_group_size_mb,
        ),
    )[:candidate_cap]
