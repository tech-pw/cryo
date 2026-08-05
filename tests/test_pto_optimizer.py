from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import LayoutRecommendation, QueryPattern, Source, Table, TableStats
from cryo.optimizer.pto.candidate_space import PtoPattern, build_candidate_space
from cryo.optimizer.pto.recommend import generate_pto_recommendation
from cryo.optimizer.pto.score_heuristic import score_candidates


def test_candidate_space_is_capped_and_drops_high_cardinality_identity() -> None:
    patterns = [
        PtoPattern("user_id", 900, 900, 0, 0.01),
        PtoPattern("region", 700, 700, 0, 0.10),
        PtoPattern("event_date", 500, 0, 500, 0.05),
    ]

    candidates = build_candidate_space(
        patterns,
        total_size_gb=500,
        total_files=10_000,
        candidate_cap=8,
    )
    ranked = score_candidates(
        candidates,
        patterns,
        total_files=10_000,
        total_size_gb=500,
        candidate_cap=8,
    )

    assert len(ranked) == 8
    assert all(candidate.candidate.partition_spec != "identity(user_id)" for candidate in ranked)
    assert any(
        (candidate.candidate.partition_spec or "").startswith("bucket[") for candidate in ranked
    )


def test_partitioning_on_selective_filter_reduces_estimated_scan_fraction() -> None:
    patterns = [PtoPattern("region", 1_000, 1_000, 0, 0.05)]
    candidates = build_candidate_space(
        patterns,
        total_size_gb=100,
        total_files=1_000,
        candidate_cap=64,
    )
    scored = score_candidates(
        candidates,
        patterns,
        total_files=1_000,
        total_size_gb=100,
        candidate_cap=64,
    )
    partitioned = next(
        item
        for item in scored
        if item.candidate.partition_spec == "identity(region)"
        and item.candidate.strategy == "binpack"
        and item.candidate.target_file_size_mb == 256
        and item.candidate.row_group_size_mb == 32
    )
    unpartitioned = next(
        item
        for item in scored
        if item.candidate.partition_spec is None
        and item.candidate.strategy == "binpack"
        and item.candidate.target_file_size_mb == 256
        and item.candidate.row_group_size_mb == 32
    )

    assert partitioned.estimated_scan_fraction < unpartitioned.estimated_scan_fraction
    assert partitioned.estimated_cost < unpartitioned.estimated_cost


def test_pto_persists_shadow_recommendation_with_low_confidence(session: Session) -> None:
    table = _seed_pto_table(session, filter_count=400)

    result = generate_pto_recommendation(session, table.id, candidate_cap=16)

    assert result.abstention_reason is None
    assert result.candidates_considered == 16
    assert result.recommendation is not None
    assert result.recommendation.status == "pending"
    assert result.recommendation.confidence == 0.35
    assert result.recommendation.detail is not None
    assert result.recommendation.detail["source"] == "pto"
    assert result.recommendation.detail["shadow"] is True
    assert result.recommendation.detail["model"] == "heuristic_v1"
    assert result.recommendation.detail["pto_params"]["target_file_size_mb"] in {
        128,
        256,
        512,
    }


def test_pto_abstains_without_enough_query_history(session: Session) -> None:
    table = _seed_pto_table(session, filter_count=20)

    result = generate_pto_recommendation(session, table.id, min_queries=200)

    assert result.recommendation is None
    assert result.abstention_reason == "insufficient_query_history"
    assert result.candidates_considered == 0


def test_pto_does_not_overwrite_another_analyzers_recommendation(session: Session) -> None:
    table = _seed_pto_table(session, filter_count=400)
    existing = LayoutRecommendation(
        id=uuid4(),
        table_id=table.id,
        recommended_strategy="sort",
        recommended_columns=[{"column": "account_id"}],
        status="approved",
        detail={"source": "layout_analyzer"},
    )
    session.add(existing)
    session.commit()

    result = generate_pto_recommendation(session, table.id)

    assert result.recommendation is None
    assert result.abstention_reason == "layout_recommendation_owned_by_other_analyzer"
    session.refresh(existing)
    assert existing.recommended_columns == [{"column": "account_id"}]
    assert existing.status == "approved"


def _seed_pto_table(session: Session, *, filter_count: int) -> Table:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=f"events_{uuid4().hex}",
        is_active=True,
    )
    session.add_all([source, table])
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=85,
            total_files=1_000,
            small_files_count=20,
            total_size_gb=100.0,
            query_count_30d=filter_count,
            raw={"partition_spec": [], "sort_order": []},
        )
    )
    session.add_all(
        [
            QueryPattern(
                id=1,
                table_id=table.id,
                column_name="region",
                filter_count=filter_count,
                equality_filter_count=filter_count,
                avg_selectivity=0.05,
            ),
            QueryPattern(
                id=2,
                table_id=table.id,
                column_name="event_date",
                filter_count=filter_count,
                range_filter_count=filter_count,
                avg_selectivity=0.1,
            ),
        ]
    )
    session.commit()
    return table
