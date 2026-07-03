from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cryo.domain.predicates import evaluate_predicate
from cryo.tiering.policies import TierCandidate, TierPolicy, evaluate_tier_candidates


def policies_from_records(
    records: list[dict[str, Any]] | list[Mapping[str, Any]],
) -> list[TierPolicy]:
    return [
        TierPolicy(
            name=str(record["name"]),
            storage_class=str(record["storage_class"]),
            min_age_days=int(record["min_age_days"]),
            predicate=record.get("predicate"),
        )
        for record in records
        if record.get("enabled", True)
    ]


def tier_candidates_for_table(
    table_ref: str,
    partitions: list[dict[str, Any]] | list[Mapping[str, Any]],
    policies: list[TierPolicy],
) -> list[TierCandidate]:
    if not policies:
        return []
    filtered_partitions = partitions
    candidates: list[TierCandidate] = []
    for policy in sorted(policies, key=lambda item: item.min_age_days):
        for partition in filtered_partitions:
            if policy.predicate and not evaluate_predicate(policy.predicate, dict(partition)):
                continue
            partition_candidates = evaluate_tier_candidates(
                table_ref,
                [partition],
                [policy],
            )
            candidates.extend(partition_candidates)
    return _dedupe_candidates(candidates)


def _dedupe_candidates(candidates: list[TierCandidate]) -> list[TierCandidate]:
    seen: set[tuple[str, str | None]] = set()
    unique: list[TierCandidate] = []
    for candidate in candidates:
        key = (candidate.table_ref, candidate.partition_value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def summarize_tier_savings(
    candidates: list[TierCandidate],
    *,
    standard_price_per_gb: float = 0.023,
    tier_price_per_gb: float = 0.0125,
) -> dict[str, Any]:
    """Rough monthly savings estimate for tiering candidates (directional only)."""
    count = len(candidates)
    if count == 0:
        return {"candidate_count": 0, "estimated_savings_usd_month": 0.0}
    # Placeholder: without per-partition bytes we estimate from candidate count only.
    estimated_gb = count * 10.0
    savings = max(0.0, (standard_price_per_gb - tier_price_per_gb) * estimated_gb)
    return {
        "candidate_count": count,
        "estimated_savings_usd_month": round(savings, 2),
        "assumptions": {
            "standard_price_per_gb": standard_price_per_gb,
            "tier_price_per_gb": tier_price_per_gb,
            "estimated_gb_per_candidate": 10.0,
        },
    }
