from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import LayoutRecommendation, QueryLog, Source, Table
from cryo.optimizer.pto import simulate_pto_recommendation


def test_simulation_uses_observed_file_touches_without_mutating_recommendation(
    session: Session,
) -> None:
    source, table, recommendation = _seed(session)
    for index in range(20):
        session.add(
            QueryLog(
                id=index + 1,
                source_id=source.id,
                table_id=table.id,
                query_id=f"query-{index}",
                engine="trino",
                executed_at=datetime.now(UTC),
                files_read=80,
                files_pruned=20,
            )
        )
    session.commit()
    original_detail = dict(recommendation.detail or {})

    result = simulate_pto_recommendation(session, table.id)

    assert result.status == "simulated"
    assert result.observations == 20
    assert result.baseline_scan_fraction == 0.8
    assert result.candidate_scan_fraction == 0.4
    assert result.estimated_improvement_pct == 50.0
    assert result.improvement_interval_pct is not None
    session.refresh(recommendation)
    assert recommendation.status == "pending"
    assert recommendation.detail == original_detail


def test_simulation_abstains_without_observed_file_touches(session: Session) -> None:
    _source, table, recommendation = _seed(session)
    session.commit()

    result = simulate_pto_recommendation(session, table.id)

    assert result.status == "abstained"
    assert result.recommendation_id == recommendation.id
    assert result.abstention_reason == "insufficient_file_pruning_observations"


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
        recommended_strategy="sort",
        recommended_columns=[{"column": "event_date"}],
        status="pending",
        detail={
            "source": "pto",
            "shadow": True,
            "model": "heuristic_v1",
            "estimated_scan_fraction": 0.4,
        },
    )
    session.add_all([source, table, recommendation])
    session.flush()
    return source, table, recommendation
