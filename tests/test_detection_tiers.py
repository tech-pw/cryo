from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.scanner.detection_tiers import DetectionTier, TierInputs, classify_detection_tier


def test_classify_detection_tier_hot() -> None:
    now = datetime.now(UTC)
    decision = classify_detection_tier(
        TierInputs(
            commits_24h=12,
            commits_7d=40,
            query_count_7d=320,
            small_files_count=8_000,
            delete_file_pct=18.0,
            last_scan_at=now - timedelta(hours=1),
        ),
        now=now,
    )
    assert decision.tier is DetectionTier.HOT
    assert decision.score >= 55


def test_classify_detection_tier_dormant() -> None:
    now = datetime.now(UTC)
    decision = classify_detection_tier(
        TierInputs(
            commits_24h=0,
            commits_7d=0,
            query_count_7d=0,
            small_files_count=5,
            delete_file_pct=0.0,
            last_scan_at=now - timedelta(days=20),
        ),
        now=now,
    )
    assert decision.tier is DetectionTier.DORMANT
