"""Regression, 2026-07-24: build_evidence crashed on every call -- QueryPattern has no
query_count/p95_latency_ms attributes (it's a per-column filter/join usage tracker, not a
query-frequency/latency tracker), so the ORDER BY threw AttributeError before any table
detail page could load its anomaly brief.
"""

from __future__ import annotations

from uuid import uuid4

from cryo.ai.anomaly_brief import build_evidence
from cryo.db.models import QueryPattern, Source, Table


def test_build_evidence_ranks_by_filter_count_not_missing_query_count(session):
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name="events",
        is_active=True,
    )
    session.add_all([source, table])
    session.flush()

    session.add_all(
        [
            QueryPattern(
                table_id=table.id,
                column_name="user_id",
                filter_count=50,
                join_count=2,
            ),
            QueryPattern(
                table_id=table.id,
                column_name="event_date",
                filter_count=200,
                join_count=0,
            ),
        ]
    )
    session.commit()

    evidence = build_evidence(session, table.id)

    assert any("event_date" in line and "200 filters" in line for line in evidence)
