"""Fleet Prometheus gauges come from SQL aggregates, not row loads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from prometheus_client import generate_latest

from cryo.db.models import (
    AuditLog,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableCost,
    TableStats,
)
from cryo.observability.fleet_metrics import publish_fleet_metrics

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _seed(session) -> Table:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    table = Table(
        id=uuid4(), source_id=source.id, schema_name="s", table_name="hot", is_active=True
    )
    session.add(table)
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=NOW - timedelta(hours=2),
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="C",
            overall_score=70,
            total_files=10,
            small_files_count=4,
            total_size_gb=2.0,
            reclaimable_bytes=1000,
        )
    )
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=NOW - timedelta(hours=2),
            precision="exact",
            price_per_gb_month=0.023,
            live_bytes=1024**3,
            dead_bytes=0,
            metadata_bytes=0,
            orphan_bytes=512,
            listed_bytes=1024**3,
            last_full_cost_scan_at=NOW - timedelta(days=3),
            reclaim_expire_bytes=0,
            reclaim_orphan_bytes=0,
            reclaim_optimize_bytes=1024**3,
            reclaim_manifests_bytes=0,
            current_monthly_cost_usd=0.023,
            projected_monthly_cost_usd=0.01,
            projected_savings_usd_month=0.013,
        )
    )
    inactive = Table(
        id=uuid4(), source_id=source.id, schema_name="s", table_name="gone", is_active=False
    )
    session.add(inactive)
    session.flush()
    session.add(
        TableStats(
            table_id=inactive.id,
            scan_at=NOW,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=100,
            total_files=1,
            small_files_count=99,
            total_size_gb=9.0,
        )
    )
    run = MaintenanceRun(id=uuid4(), kind="adhoc", trigger="test", status="running")
    session.add(run)
    session.flush()
    session.add(
        MaintenanceJob(
            id=uuid4(),
            run_id=run.id,
            table_id=table.id,
            destination_id=_destination(session).id,
            mode="standard",
            status="running",
        )
    )
    session.add(
        AuditLog(
            ts=NOW - timedelta(days=1),
            table_id=table.id,
            action="optimize",
            status="succeeded",
            actual_bytes_freed=1024**3 // 2,
        )
    )
    session.commit()
    return table


def _destination(session):
    from cryo.db.models import Destination

    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="spark_emr", config={})
    session.add(destination)
    session.flush()
    return destination


def test_publish_fleet_metrics_counts_active_tables_only(session) -> None:
    _seed(session)

    publish_fleet_metrics(session, now=NOW)

    body = generate_latest().decode()
    assert 'cryo_datalake_grade_tables{grade="C"} 1.0' in body
    assert 'cryo_datalake_grade_tables{grade="A"} 0.0' in body
    assert "cryo_datalake_small_files 4.0" in body
    assert 'cryo_scan_coverage_ratio{window="1d"} 1.0' in body
    assert 'cryo_reclaimable_bytes{operation="optimize"}' in body
    assert "cryo_orphan_bytes 512.0" in body
    assert "cryo_realized_vs_projected_ratio 0.5" in body
