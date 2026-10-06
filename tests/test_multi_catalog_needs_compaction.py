"""Multi-catalog needs_compaction matches Health's TableStats.needs_compaction flag."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.api.dashboard_routes import get_multi_catalog_stats
from cryo.db.models import Source, Table, TableStats
from cryo.db.repositories.datalake import dashboard_datalake


def test_multi_catalog_needs_compaction_matches_health_flag(session) -> None:
    source = Source(
        name=f"catalog-{uuid.uuid4().hex[:8]}",
        type="rest",
        config={},
        enabled=True,
    )
    session.add(source)
    session.flush()
    scan_at = datetime(2026, 9, 28, 12, tzinfo=UTC)
    for name, small_files, needs_compaction in (
        ("many_small_files_not_flagged", 200, False),
        ("flagged_few_small_files", 10, True),
        ("flagged_no_small_files", 0, True),
    ):
        table = Table(
            source_id=source.id,
            schema_name="analytics",
            table_name=name,
            first_seen_at=scan_at,
            is_active=True,
        )
        session.add(table)
        session.flush()
        session.add(
            TableStats(
                table_id=table.id,
                scan_at=scan_at,
                needs_compaction=needs_compaction,
                compaction_priority="LOW",
                grade="C",
                overall_score=60,
                total_files=small_files + 1,
                small_files_count=small_files,
                total_size_gb=1.0,
                query_count_30d=0,
            )
        )
    session.flush()

    multi = get_multi_catalog_stats(session, {})
    health_count = int(dashboard_datalake(session)["totals"]["needs_compaction_count"])

    assert len(multi.catalogs) == 1
    assert multi.catalogs[0].needs_compaction_count == health_count == 2
