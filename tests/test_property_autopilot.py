"""Tests for the property-recommendation autopilot (cryo/scanner/property_autopilot.py).

Regression context, 2026-08-10: compute_property_recommendations() (generation) and
apply_table_properties() (application) were both already fully built -- the former running
on a schedule via layout_analyzer, the latter live behind a manual-only API route -- but
nothing in the codebase had ever connected them. These tests exercise the module that
closes that gap, with particular focus on what it must NOT touch (tier2/tier3/
needs_confirmation) since that's the actual safety boundary.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from cryo.db.models import AuditLog, Source, Table, TableStats
from cryo.scanner.property_autopilot import AUTOPILOT_ACTOR, run_property_autopilot


def _seed_table(
    session,
    *,
    schema_name: str = "analytics",
    table_name: str = "events",
    target_bytes: int | None = None,
    write_mode: str | None = None,
    has_sort: bool = False,
    manifest_count: int = 220,
    total_snapshots: int = 80,
    raw_extra: dict | None = None,
) -> uuid.UUID:
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    raw = {
        "iceberg_properties": {},
        "equality_delete_files": 50,
        "position_delete_files": 10,
        "commits_per_day": 30.0,
        "is_streaming": True,
        "v3_upgrade_candidate": True,
        "format_version": 2,
        "gc_enabled": True,
    }
    if raw_extra:
        raw.update(raw_extra)
    session.add(
        Source(
            id=source_id,
            name=f"lake-{source_id}",
            type="rest",
            config={"catalog_name": "iceberg", "type": "rest", "uri": "http://localhost"},
        )
    )
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name=schema_name,
            table_name=table_name,
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="C",
            overall_score=65,
            total_files=120_000,
            small_files_count=45_000,
            total_size_gb=480.0,
            avg_file_size_mb=41.0,
            total_partitions=120,
            partition_skew_ratio=18.5,
            manifest_count=manifest_count,
            total_snapshots=total_snapshots,
            delete_file_pct=12.5,
            total_delete_files=1500,
            write_distribution_mode=write_mode,
            has_sort_order=has_sort,
            target_file_size_bytes=target_bytes,
            query_count_30d=0,
            raw=raw,
        )
    )
    session.commit()
    return table_id


def _mock_catalog():
    mock_table = MagicMock()
    mock_txn = MagicMock()
    mock_table.transaction.return_value.__enter__ = MagicMock(return_value=mock_txn)
    mock_table.transaction.return_value.__exit__ = MagicMock(return_value=False)
    return mock_table, mock_txn


def test_property_autopilot_applies_tier1_and_audits_with_distinct_actor(session) -> None:
    table_id = _seed_table(session, target_bytes=None, write_mode=None)

    mock_table, mock_txn = _mock_catalog()
    with patch("cryo.sources.pyiceberg_catalog.PyIcebergSourceCatalog") as catalog_cls:
        catalog_cls.return_value.load_table.return_value = mock_table
        result = run_property_autopilot(session, max_tables=10)

    assert result.tables_considered == 1
    assert result.tables_changed == 1
    assert result.tables_failed == 0
    mock_txn.set_properties.assert_called_once()
    applied = mock_txn.set_properties.call_args.kwargs
    assert "write.target-file-size-bytes" in applied
    assert "write.distribution-mode" in applied

    audit = session.query(AuditLog).filter(AuditLog.table_id == table_id).one()
    assert audit.actor == AUTOPILOT_ACTOR
    assert audit.action == "apply_table_properties"
    assert "autonomous tier1 apply" in audit.detail


def test_property_autopilot_never_touches_sort_order_or_risky_properties(session) -> None:
    """write.sort-order (tier2, link_layout -- a real data rewrite) and format-version
    (needs_confirmation -- risky) must never appear in what autopilot applies, even though
    both can appear in the same table's recommendation list as tier1 items do."""
    table_id = _seed_table(session, target_bytes=None, write_mode=None, has_sort=False)

    mock_table, mock_txn = _mock_catalog()
    with patch("cryo.sources.pyiceberg_catalog.PyIcebergSourceCatalog") as catalog_cls:
        catalog_cls.return_value.load_table.return_value = mock_table
        run_property_autopilot(session, max_tables=10)

    applied = mock_txn.set_properties.call_args.kwargs
    assert "write.sort-order" not in applied
    assert "format-version" not in applied
    # Sanity: the seeded table genuinely has a v3_upgrade_candidate / no-sort-order
    # recommendation available, so their absence here is the filter working, not a fluke.
    from cryo.scanner.property_recommendations import compute_property_recommendations

    all_items = compute_property_recommendations(session, table_id)
    keys_available = {item.property_key for item in all_items}
    assert "write.sort-order" in keys_available
    assert "format-version" in keys_available


def test_property_autopilot_no_op_when_already_optimal(session) -> None:
    """A table with nothing left to recommend should be silently skipped -- no apply call,
    no audit row, not counted as changed."""
    table_id = _seed_table(
        session,
        # _DEFAULT_TARGET_BYTES (256MB): avg_file_size_mb=41 (below the 100MB/1000-small-file
        # cutoff for _LARGE_TARGET_BYTES) -- must match exactly what
        # _recommend_target_file_size() would compute, or this table isn't actually optimal.
        target_bytes=268_435_456,
        write_mode="hash",
        has_sort=True,
        manifest_count=5,
        total_snapshots=5,
        raw_extra={
            "iceberg_properties": {
                "write.target-file-size-bytes": "268435456",
                "write.distribution-mode": "hash",
                "write.metadata.delete-after-commit.enabled": "true",
                "write.metadata.previous-versions-max": "10",
                "commit.retry.num-retries": "10",
                "commit.retry.min-wait-ms": "100",
                "commit.retry.max-wait-ms": "60000",
                "write.parquet.compression-codec": "zstd",
                "write.sort-order": "sort",
                "write.merge-mode": "merge-on-read",
            },
            "commits_per_day": 30.0,
            "is_streaming": True,
        },
    )

    with patch("cryo.sources.pyiceberg_catalog.PyIcebergSourceCatalog") as catalog_cls:
        result = run_property_autopilot(session, max_tables=10)
        catalog_cls.assert_not_called()

    assert result.tables_considered == 1
    assert result.tables_changed == 0
    assert result.tables_failed == 0
    assert session.query(AuditLog).filter(AuditLog.table_id == table_id).count() == 0


def test_property_autopilot_respects_max_tables(session) -> None:
    for i in range(5):
        _seed_table(session, schema_name="analytics", table_name=f"events_{i}")

    mock_table, _ = _mock_catalog()
    with patch("cryo.sources.pyiceberg_catalog.PyIcebergSourceCatalog") as catalog_cls:
        catalog_cls.return_value.load_table.return_value = mock_table
        result = run_property_autopilot(session, max_tables=2)

    assert result.tables_considered == 2


def test_property_autopilot_dry_run_does_not_call_apply(session) -> None:
    _seed_table(session, target_bytes=None, write_mode=None)

    with patch("cryo.sources.pyiceberg_catalog.PyIcebergSourceCatalog") as catalog_cls:
        result = run_property_autopilot(session, max_tables=10, dry_run=True)
        catalog_cls.assert_not_called()

    assert result.tables_changed == 1
    assert result.per_table[0].applied  # reports what WOULD be applied
    assert session.query(AuditLog).count() == 0


def test_property_autopilot_isolates_one_table_apply_failure_from_the_rest(session) -> None:
    failing_table_id = _seed_table(
        session, schema_name="analytics", table_name="failing", target_bytes=None
    )
    ok_table_id = _seed_table(session, schema_name="analytics", table_name="ok", target_bytes=None)

    mock_table, mock_txn = _mock_catalog()

    def load_table_side_effect(schema, name):
        if name == "failing":
            raise RuntimeError("catalog unreachable")
        return mock_table

    with patch("cryo.sources.pyiceberg_catalog.PyIcebergSourceCatalog") as catalog_cls:
        catalog_cls.return_value.load_table.side_effect = load_table_side_effect
        result = run_property_autopilot(session, max_tables=10)

    assert result.tables_considered == 2
    assert result.tables_changed == 1
    assert result.tables_failed == 1
    failed = next(r for r in result.per_table if r.table_id == failing_table_id)
    assert failed.error is not None
    ok = next(r for r in result.per_table if r.table_id == ok_table_id)
    assert ok.error is None
    assert ok.applied
