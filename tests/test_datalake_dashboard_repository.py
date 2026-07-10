"""Integration tests for cryo/db/repositories/datalake.py's dashboard_datalake().

Regression coverage for a real production bug: the Health dashboard's "Monthly savings" could
show larger than "S3 monthly now" itself, because the totals mixed TableStats.
estimated_monthly_s3_cost_usd (a cheap size-only heuristic refreshed on every scan) with
TableStats.projected_savings_usd_month (computed by project_savings() against a *different*,
more complete deep-tier cost baseline that was never itself surfaced). dashboard_datalake now
reuses dashboard_cost's TableCost-based aggregation, which reconciles by construction. This
also covers the "Cost by schema" panel, which was always empty because dashboard_datalake never
returned a cost_by_schema key at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.db.models import Source, Table, TableCost, TableStats
from cryo.db.repositories.datalake import dashboard_datalake


def _seed_source(session) -> Source:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def test_dashboard_datalake_cost_totals_reconcile_when_table_cost_exists(session) -> None:
    source = _seed_source(session)
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    table = Table(
        id=table_id,
        source_id=source.id,
        schema_name="analytics",
        table_name="events",
        first_seen_at=scan_at,
        is_active=True,
    )
    session.add(table)
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=100,
            small_files_count=5,
            total_size_gb=50.0,
            query_count_30d=0,
            # Deliberately mismatched heuristic vs. the TableCost row below -- this is the
            # exact data shape that used to leak into the dashboard totals and make "Monthly
            # savings" exceed "S3 monthly now".
            estimated_monthly_s3_cost_usd=10.0,
            reclaimable_bytes=1000,
        )
    )
    session.add(
        TableCost(
            table_id=table_id,
            scan_at=scan_at,
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=1000,
            dead_bytes=500,
            metadata_bytes=50,
            orphan_bytes=0,
            listed_bytes=1550,
            reclaim_expire_bytes=100,
            reclaim_orphan_bytes=0,
            reclaim_optimize_bytes=0,
            reclaim_manifests_bytes=0,
            current_monthly_cost_usd=100.0,
            projected_monthly_cost_usd=55.0,
            projected_savings_usd_month=45.0,
            query_request_savings_usd_month=0.0,
        )
    )
    session.commit()

    result = dashboard_datalake(session)

    totals = result["totals"]
    # The TableCost row's self-consistent triple wins, not the mismatched TableStats heuristic.
    assert totals["estimated_monthly_s3_cost_usd"] == 100.0
    assert totals["projected_monthly_s3_cost_usd"] == 55.0
    assert totals["projected_savings_usd_month"] == 45.0
    # Savings must never exceed the current cost it's supposedly netting against.
    assert totals["projected_savings_usd_month"] <= totals["estimated_monthly_s3_cost_usd"]
    assert (
        totals["estimated_monthly_s3_cost_usd"] - totals["projected_monthly_s3_cost_usd"]
        == totals["projected_savings_usd_month"]
    )


def test_dashboard_datalake_populates_cost_by_schema(session) -> None:
    source = _seed_source(session)
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    for schema_name, current, projected, savings in [
        ("analytics", 80.0, 40.0, 40.0),
        ("billing", 20.0, 15.0, 5.0),
    ]:
        table_id = uuid.uuid4()
        session.add(
            Table(
                id=table_id,
                source_id=source.id,
                schema_name=schema_name,
                table_name="t",
                first_seen_at=scan_at,
                is_active=True,
            )
        )
        session.add(
            TableCost(
                table_id=table_id,
                scan_at=scan_at,
                precision="metadata",
                price_per_gb_month=0.023,
                live_bytes=1000,
                dead_bytes=0,
                metadata_bytes=0,
                orphan_bytes=0,
                listed_bytes=1000,
                reclaim_expire_bytes=0,
                reclaim_orphan_bytes=0,
                reclaim_optimize_bytes=0,
                reclaim_manifests_bytes=0,
                current_monthly_cost_usd=current,
                projected_monthly_cost_usd=projected,
                projected_savings_usd_month=savings,
                query_request_savings_usd_month=0.0,
            )
        )
    session.commit()

    result = dashboard_datalake(session)

    by_schema = {row["schema_name"]: row for row in result["cost_by_schema"]}
    assert by_schema["analytics"]["current_usd"] == 80.0
    assert by_schema["billing"]["savings_usd"] == 5.0


def test_dashboard_datalake_falls_back_to_table_stats_without_table_cost(session) -> None:
    """A table that's never had a deep-tier cost scan (no TableCost row yet) must still
    contribute something reasonable, not silently drop out of the totals."""
    source = _seed_source(session)
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="demo",
            table_name="orders",
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=1,
            total_size_gb=5.0,
            query_count_30d=0,
            estimated_monthly_s3_cost_usd=12.0,
            reclaimable_bytes=0,
        )
    )
    session.commit()

    result = dashboard_datalake(session)

    assert result["totals"]["estimated_monthly_s3_cost_usd"] == 12.0
