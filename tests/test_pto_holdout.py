from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, LayoutRecommendation, QueryLog, Source, Table
from cryo.optimizer.pto import evaluate_pto_holdout, record_pto_holdout


def test_temporal_holdout_is_advisory_without_counterfactual_labels(session: Session) -> None:
    source, table, recommendation = _seed(session)
    now = datetime.now(UTC)
    for index in range(40):
        session.add(
            QueryLog(
                id=index + 1,
                source_id=source.id,
                table_id=table.id,
                query_id=f"query-{index}",
                engine="trino",
                executed_at=now - timedelta(minutes=40 - index),
                files_read=80 if index < 20 else 70,
                files_pruned=20 if index < 20 else 30,
            )
        )
    session.commit()

    result = record_pto_holdout(session, table.id, actor="operator-1")

    assert result.status == "evaluated"
    assert result.train_scan_fraction == 0.8
    assert result.holdout_scan_fraction == 0.7
    assert result.stability_delta == 0.1
    assert result.holdout_improvement_pct == 42.86
    assert result.qualifies_for_canary is False
    session.refresh(recommendation)
    assert (recommendation.detail or {})["holdout_evidence"]["schema"] == "pto-holdout/v1"
    audit = session.query(AuditLog).filter_by(table_id=table.id).one()
    assert audit.status == "ADVISORY"


def test_temporal_holdout_abstains_when_split_is_too_small(session: Session) -> None:
    _source, table, _recommendation = _seed(session)
    session.commit()

    result = evaluate_pto_holdout(session, table.id)

    assert result.status == "abstained"
    assert result.abstention_reason == "insufficient_temporal_holdout_observations"
    assert result.qualifies_for_canary is False


def _seed(session: Session) -> tuple[Source, Table, LayoutRecommendation]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=f"events_{uuid4().hex}",
        is_active=True,
    )
    recommendation = LayoutRecommendation(
        id=uuid4(),
        table_id=table.id,
        recommended_strategy="binpack",
        recommended_columns=[],
        status="pending",
        detail={
            "source": "pto",
            "shadow": True,
            "estimated_scan_fraction": 0.4,
        },
    )
    session.add_all([source, table, recommendation])
    session.flush()
    return source, table, recommendation
