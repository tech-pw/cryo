from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import LayoutRecommendation, QueryPattern, Source, Table
from cryo.optimizer.pto import assess_pto_drift


def test_pto_drift_detects_material_workload_change(session: Session) -> None:
    table = _seed(session)
    session.add_all(
        [
            QueryPattern(
                id=1,
                table_id=table.id,
                column_name="region",
                filter_count=10,
                equality_filter_count=10,
                avg_selectivity=0.1,
            ),
            QueryPattern(
                id=2,
                table_id=table.id,
                column_name="account_id",
                filter_count=900,
                equality_filter_count=900,
                avg_selectivity=0.01,
            ),
        ]
    )
    session.commit()

    result = assess_pto_drift(session, table.id)

    assert result.status == "drifted"
    assert result.distance is not None
    assert result.distance > result.threshold


def test_pto_drift_reports_stable_matching_distribution(session: Session) -> None:
    table = _seed(session)
    session.add(
        QueryPattern(
            id=1,
            table_id=table.id,
            column_name="region",
            filter_count=100,
            equality_filter_count=100,
            avg_selectivity=0.1,
        )
    )
    session.commit()

    result = assess_pto_drift(session, table.id)

    assert result.status == "stable"
    assert result.distance == 0.0


def _seed(session: Session) -> Table:
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
        recommended_columns=[{"column": "region"}],
        status="pending",
        detail={
            "source": "pto",
            "shadow": True,
            "pattern_distribution": {"region": 1.0},
        },
    )
    session.add_all([source, table, recommendation])
    session.flush()
    return table
