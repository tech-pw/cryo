from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class TierPolicy:
    name: str
    storage_class: str
    min_age_days: int
    predicate: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class TierCandidate:
    table_ref: str
    partition_value: str | None
    age_days: int
    target_storage_class: str


def evaluate_tier_candidates(
    table_ref: str,
    partitions: list[Mapping[str, Any]],
    policies: list[TierPolicy],
    *,
    now: datetime | None = None,
) -> list[TierCandidate]:
    current = now or datetime.now(UTC)
    candidates: list[TierCandidate] = []
    for policy in sorted(policies, key=lambda item: item.min_age_days):
        for partition in partitions:
            last_write = partition.get("last_write_at") or partition.get("updated_at")
            if last_write is None:
                continue
            age_days = _age_days(last_write, current)
            if age_days < policy.min_age_days:
                continue
            candidates.append(
                TierCandidate(
                    table_ref=table_ref,
                    partition_value=str(partition.get("partition_value")),
                    age_days=age_days,
                    target_storage_class=policy.storage_class,
                )
            )
    return candidates


def _age_days(value: Any, now: datetime) -> int:
    if isinstance(value, datetime):
        observed = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return max((now - observed).days, 0)
