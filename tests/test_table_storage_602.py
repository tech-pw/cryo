"""Per-table storage columns, bucket grouping and storage filters on /tables (#602)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from cryo.api.routes import group_tables, list_tables
from cryo.db.models import Source, Table, TableCost, TableStats
from cryo.db.table_storage import bucket_of, storage_fields

GIB = 1024**3
NOW = datetime(2026, 11, 19, tzinfo=UTC)


def _seed(
    session,
    source: Source,
    *,
    name: str,
    location: str | None,
    live: int,
    dead: int,
    orphan: int | None,
    meta: int,
    listed: int | None,
) -> Table:
    table = Table(
        source_id=source.id,
        schema_name="finance",
        table_name=name,
        is_active=True,
        location=location,
    )
    session.add(table)
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=NOW,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=1,
            total_size_gb=1.0,
        )
    )
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=NOW,
            precision="storage_truth",
            price_per_gb_month=0.023,
            live_bytes=live,
            dead_bytes=dead,
            metadata_bytes=meta,
            orphan_bytes=orphan,
            listed_bytes=listed,
            last_full_cost_scan_at=NOW if listed is not None else None,
            current_monthly_cost_usd=1.0,
            projected_monthly_cost_usd=1.0,
            projected_savings_usd_month=0.0,
        )
    )
    session.commit()
    return table


def _fleet(session) -> None:
    source = Source(name="lake", type="hive", config={})
    session.add(source)
    session.flush()
    # listed: 10 live + 2 dead + 1 meta + 4 orphan = 17; listed 20 -> 3 retained history
    _seed(
        session,
        source,
        name="orders",
        location="s3a://audit-bucket/gold/orders",
        live=10 * GIB,
        dead=2 * GIB,
        orphan=4 * GIB,
        meta=GIB,
        listed=20 * GIB,
    )
    _seed(
        session,
        source,
        name="users",
        location="s3://audit-bucket/gold/users",
        live=5 * GIB,
        dead=0,
        orphan=None,
        meta=GIB,
        listed=None,
    )
    _seed(
        session,
        source,
        name="events",
        location="s3a://bronze-bucket/events",
        live=GIB,
        dead=GIB,
        orphan=0,
        meta=0,
        listed=2 * GIB,
    )


def _list(session, **kwargs):
    defaults = dict(
        filter=None,
        sort=None,
        limit=100,
        offset=0,
        cursor=None,
        freshness_breach=False,
        maintenance_overdue=False,
        p95_breach=False,
        no_reads_days=None,
        removed=None,
        include_removed=False,
        effective_provenance=None,
    )
    return list_tables(session, **{**defaults, **kwargs})


def test_bucket_of_handles_schemes_and_non_object_paths() -> None:
    assert bucket_of("s3a://audit-bucket/gold/orders") == "audit-bucket"
    assert bucket_of("s3://b") == "b"
    assert bucket_of("/local/warehouse/t") is None
    assert bucket_of(None) is None


def test_storage_fields_split_snapshot_and_junk() -> None:
    cost = TableCost(live_bytes=10, dead_bytes=2, metadata_bytes=1, orphan_bytes=4, listed_bytes=20)
    assert storage_fields(cost) == {
        "live_bytes": 10,
        "snapshot_bytes": 5,
        "orphan_bytes": 4,
        "metadata_bytes": 1,
        "junk_bytes": 9,
    }
    unlisted = TableCost(
        live_bytes=10, dead_bytes=2, metadata_bytes=1, orphan_bytes=7, listed_bytes=None
    )
    assert storage_fields(unlisted)["orphan_bytes"] is None
    assert storage_fields(unlisted)["junk_bytes"] == 2


def test_list_returns_bucket_and_storage_columns(session) -> None:
    _fleet(session)
    by_name = {t.table_name: t for t in _list(session).items}
    orders = by_name["orders"]
    assert orders.bucket == "audit-bucket"
    assert orders.stats.live_bytes == 10 * GIB
    assert orders.stats.snapshot_bytes == 5 * GIB
    assert orders.stats.orphan_bytes == 4 * GIB
    assert orders.stats.junk_bytes == 9 * GIB
    assert by_name["users"].stats.orphan_bytes is None


def test_filter_by_bucket_and_orphan_bytes(session) -> None:
    _fleet(session)
    in_bucket = _list(
        session, filter=json.dumps({"field": "bucket", "op": "in", "value": ["audit-bucket"]})
    )
    assert {t.table_name for t in in_bucket.items} == {"orders", "users"}
    with_orphans = _list(
        session, filter=json.dumps({"field": "orphan_bytes", "op": ">", "value": 0})
    )
    assert [t.table_name for t in with_orphans.items] == ["orders"]


def test_sort_by_junk_bytes_descending(session) -> None:
    _fleet(session)
    names = [t.table_name for t in _list(session, sort="-junk_bytes").items]
    assert names == ["orders", "events", "users"]


def test_group_by_bucket_sums_storage(session) -> None:
    _fleet(session)
    rows = {row.group_value: row for row in group_tables(session, field="bucket").rows}
    audit = rows["audit-bucket"]
    assert audit.count == 2
    assert audit.total_live_bytes == 15 * GIB
    assert audit.total_orphan_bytes == 4 * GIB
    assert audit.total_junk_bytes == 9 * GIB + 0
    assert rows["bronze-bucket"].total_snapshot_bytes == GIB
