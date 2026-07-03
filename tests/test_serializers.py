from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.api.serializers import table_to_read
from cryo.db.models import Table, TableCost, TableStats
from cryo.domain.enums import CostPrecision


def test_table_serializer_exposes_cost_precision_context() -> None:
    table_id = uuid.uuid4()
    source_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 3, tzinfo=UTC)
    last_full_scan_at = datetime(2026, 7, 2, tzinfo=UTC)
    table = Table(
        id=table_id,
        source_id=source_id,
        schema_name="demo",
        table_name="orders",
        first_seen_at=scan_at,
        is_active=True,
    )
    table.stats = TableStats(
        table_id=table_id,
        scan_at=scan_at,
        needs_compaction=True,
        compaction_priority="HIGH",
        grade="C",
        overall_score=72,
        total_files=420,
        small_files_count=120,
        total_size_gb=12.5,
        query_count_30d=0,
    )
    table.cost = TableCost(
        table_id=table_id,
        scan_at=scan_at,
        precision=CostPrecision.STORAGE_TRUTH.value,
        price_per_gb_month=0.023,
        live_bytes=1000,
        dead_bytes=200,
        metadata_bytes=50,
        orphan_bytes=25,
        listed_bytes=1225,
        last_full_cost_scan_at=last_full_scan_at,
        reclaim_expire_bytes=100,
        reclaim_orphan_bytes=25,
        reclaim_optimize_bytes=50,
        reclaim_manifests_bytes=0,
        current_monthly_cost_usd=1.0,
        projected_monthly_cost_usd=0.7,
        projected_savings_usd_month=0.3,
        query_request_savings_usd_month=0.0,
    )

    read = table_to_read(table)

    assert read.stats is not None
    assert read.stats.cost_precision == CostPrecision.STORAGE_TRUTH
    assert read.stats.cost_last_full_scan_at == last_full_scan_at
