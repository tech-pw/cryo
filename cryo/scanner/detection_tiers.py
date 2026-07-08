from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class DetectionTier(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    DORMANT = "dormant"


@dataclass(frozen=True)
class TierInputs:
    commits_24h: int
    commits_7d: int
    query_count_7d: int
    small_files_count: int
    delete_file_pct: float
    last_scan_at: datetime | None


@dataclass(frozen=True)
class TierDecision:
    tier: DetectionTier
    score: float
    reason: str


def classify_detection_tier(inputs: TierInputs, now: datetime | None = None) -> TierDecision:
    """Classify a table into HOT/WARM/COLD/DORMANT.

    The score is intentionally simple and monotonic so operators can reason
    about transitions and tune thresholds without retraining a model.
    """
    current = now or datetime.now(UTC)
    staleness_days = 999.0
    if inputs.last_scan_at is not None:
        staleness_days = max(
            0.0,
            (current - inputs.last_scan_at.astimezone(UTC)).total_seconds() / timedelta(days=1).total_seconds(),
        )

    score = (
        inputs.commits_24h * 5.0
        + inputs.commits_7d * 1.25
        + inputs.query_count_7d * 0.12
        + min(inputs.small_files_count, 20_000) / 180.0
        + min(max(inputs.delete_file_pct, 0.0), 100.0) * 0.4
        - min(staleness_days, 30.0) * 1.0
    )

    if inputs.commits_7d == 0 and inputs.query_count_7d <= 1 and staleness_days >= 14:
        return TierDecision(
            tier=DetectionTier.DORMANT,
            score=score,
            reason="No recent commits/query activity and stale scan history.",
        )
    if score >= 55:
        return TierDecision(
            tier=DetectionTier.HOT,
            score=score,
            reason="High write/read churn or compaction pressure.",
        )
    if score >= 22:
        return TierDecision(
            tier=DetectionTier.WARM,
            score=score,
            reason="Moderate activity with meaningful maintenance payoff.",
        )
    return TierDecision(
        tier=DetectionTier.COLD,
        score=score,
        reason="Low activity; safe to deprioritize routine maintenance.",
    )
