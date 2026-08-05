from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, LayoutRecommendation, QueryLog


@dataclass(frozen=True, slots=True)
class PtoHoldoutResult:
    recommendation_id: uuid.UUID | None
    status: str
    method: str
    train_observations: int
    holdout_observations: int
    train_scan_fraction: float | None = None
    holdout_scan_fraction: float | None = None
    stability_delta: float | None = None
    holdout_improvement_pct: float | None = None
    qualifies_for_canary: bool = False
    qualification_reason: str = "counterfactual_labels_unavailable"
    abstention_reason: str | None = None


def evaluate_pto_holdout(
    session: Session,
    table_id: uuid.UUID,
    *,
    min_split_observations: int = 20,
    history_days: int = 60,
) -> PtoHoldoutResult:
    recommendation = session.scalar(
        select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
    )
    if recommendation is None or (recommendation.detail or {}).get("source") != "pto":
        return _abstain("missing_pto_recommendation")
    candidate_fraction = (recommendation.detail or {}).get("estimated_scan_fraction")
    if not isinstance(candidate_fraction, int | float) or not 0 <= candidate_fraction <= 1:
        return _abstain("missing_candidate_scan_estimate", recommendation.id)

    cutoff = datetime.now(UTC) - timedelta(days=history_days)
    logs = list(
        session.scalars(
            select(QueryLog)
            .where(
                QueryLog.table_id == table_id,
                QueryLog.executed_at >= cutoff,
                QueryLog.files_read.is_not(None),
                QueryLog.files_pruned.is_not(None),
            )
            .order_by(QueryLog.executed_at, QueryLog.id)
        )
    )
    observations = [
        (int(log.files_read or 0), int(log.files_pruned or 0))
        for log in logs
        if int(log.files_read or 0) + int(log.files_pruned or 0) > 0
    ]
    required = min_split_observations * 2
    if len(observations) < required:
        return _abstain(
            "insufficient_temporal_holdout_observations",
            recommendation.id,
            len(observations) // 2,
            len(observations) - len(observations) // 2,
        )

    split = len(observations) // 2
    train = observations[:split]
    holdout = observations[split:]
    train_fraction = _scan_fraction(train)
    holdout_fraction = _scan_fraction(holdout)
    if holdout_fraction <= 0:
        return _abstain(
            "zero_holdout_scan_fraction",
            recommendation.id,
            len(train),
            len(holdout),
        )
    improvement = (holdout_fraction - float(candidate_fraction)) / holdout_fraction * 100
    return PtoHoldoutResult(
        recommendation_id=recommendation.id,
        status="evaluated",
        method="temporal_baseline_replay_v1",
        train_observations=len(train),
        holdout_observations=len(holdout),
        train_scan_fraction=round(train_fraction, 6),
        holdout_scan_fraction=round(holdout_fraction, 6),
        stability_delta=round(abs(train_fraction - holdout_fraction), 6),
        holdout_improvement_pct=round(improvement, 2),
    )


def record_pto_holdout(
    session: Session,
    table_id: uuid.UUID,
    *,
    actor: str,
) -> PtoHoldoutResult:
    result = evaluate_pto_holdout(session, table_id)
    if result.recommendation_id is None:
        return result
    recommendation = session.get(LayoutRecommendation, result.recommendation_id)
    if recommendation is None:
        raise ValueError(f"Recommendation not found: {result.recommendation_id}")
    report: dict[str, object] = asdict(result)
    report["recommendation_id"] = str(result.recommendation_id)
    evidence: dict[str, object] = {
        "schema": "pto-holdout/v1",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "report": report,
    }
    detail = dict(recommendation.detail or {})
    detail["holdout_evidence"] = evidence
    recommendation.detail = detail
    session.add(
        AuditLog(
            table_id=table_id,
            action="pto_holdout_recorded",
            status="ABSTAINED" if result.status == "abstained" else "ADVISORY",
            engine="pto_shadow",
            detail=json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            actor=actor,
        )
    )
    session.commit()
    return result


def _scan_fraction(observations: list[tuple[int, int]]) -> float:
    files_read = sum(read for read, _pruned in observations)
    opportunities = sum(read + pruned for read, pruned in observations)
    return files_read / opportunities


def _abstain(
    reason: str,
    recommendation_id: uuid.UUID | None = None,
    train_observations: int = 0,
    holdout_observations: int = 0,
) -> PtoHoldoutResult:
    return PtoHoldoutResult(
        recommendation_id=recommendation_id,
        status="abstained",
        method="temporal_baseline_replay_v1",
        train_observations=train_observations,
        holdout_observations=holdout_observations,
        abstention_reason=reason,
    )
