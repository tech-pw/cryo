"""Parity + memory-path tests for the Iceberg metadata scanner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from cryo.config import Settings
from cryo.scanner.analyze import analyze_table, iter_data_files
from cryo.scanner.go_scan import table_analysis_from_go_payload
from tests.test_scanner import FakeDataFile, FakeSnapshotMeta, FakeTable

MB = 1024 * 1024


def test_iter_data_files_never_calls_plan_files() -> None:
    """Regression: stats must walk manifests, not materialize scan().plan_files()."""

    class BoomScan:
        def plan_files(self) -> list[object]:
            raise AssertionError("plan_files must not be used for stats scans")

    class TableWithScan(FakeTable):
        def scan(self) -> BoomScan:
            return BoomScan()

    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = TableWithScan(
        files=[FakeDataFile(10 * MB, 10, ("2026-06-29",))],
        snapshots=[FakeSnapshotMeta(scan_at)],
    )
    files = list(iter_data_files(table, parallel=False))
    assert len(files) == 1


def test_analyze_skips_metadata_cost_when_file_count_high() -> None:
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = FakeTable(
        files=[FakeDataFile(10 * MB, 10, ("2026-06-29",)) for _ in range(5)],
        snapshots=[FakeSnapshotMeta(scan_at)],
    )
    settings = Settings(
        cost_scan_tier="metadata",
        cost_metadata_max_files_estimate=3,
    )
    analysis = analyze_table(table, "demo", "events", scan_at=scan_at, settings=settings)
    assert analysis.cost is None
    assert analysis.estimated_monthly_s3_cost_usd >= 0


def _python_analysis_fixture() -> Any:
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = FakeTable(
        files=[
            FakeDataFile(10 * MB, 10, ("2026-06-29",)),
            FakeDataFile(20 * MB, 20, ("2026-06-29",)),
            FakeDataFile(30 * MB, 30, ("2026-06-29",)),
            FakeDataFile(256 * MB, 100, ("2026-06-30",)),
        ],
        snapshots=[FakeSnapshotMeta(scan_at - timedelta(days=1))],
    )
    return analyze_table(
        table,
        "demo",
        "events",
        scan_at=scan_at,
        settings=Settings(cost_scan_tier="heuristic"),
    )


def test_go_payload_mapper_matches_python_day_partitioned_totals() -> None:
    """Shared fixture: Python FakeTable vs Go AggregateFromRecords-shaped JSON."""
    py = _python_analysis_fixture()
    go_payload = {
        "ok": True,
        "total_files": 4,
        "total_records": 160,
        "total_size_bytes": (10 + 20 + 30 + 256) * MB,
        "health_small_files": 0,
        "delete_files": 0,
        "equality_delete_files": 0,
        "position_delete_files": 0,
        "delete_bytes": 0,
        "manifest_count": 1,
        "manifest_total_bytes": 1024,
        "total_snapshots": 1,
        "file_formats": ["PARQUET"],
        "partition_fields": [
            {
                "base_column": "event_date",
                "partition_field": "event_date_day",
                "transform": "day",
                "transform_param": None,
                "col_type": "date",
            }
        ],
        "partitions": [
            {
                "partition_key": "event_date_day=2026-06-29",
                "partition_value": "2026-06-29",
                "partition_values": {"event_date_day": "2026-06-29"},
                "partition_spec_id": 0,
                "files": 3,
                "small": 3,
                "size_bytes": (10 + 20 + 30) * MB,
            },
            {
                "partition_key": "event_date_day=2026-06-30",
                "partition_value": "2026-06-30",
                "partition_values": {"event_date_day": "2026-06-30"},
                "partition_spec_id": 0,
                "files": 1,
                "small": 0,
                "size_bytes": 256 * MB,
            },
        ],
        "write_distribution_mode": "hash",
        "has_sort_order": True,
        "target_file_size_bytes": 268435456,
        "location": None,
        "format_version": 2,
    }
    mapped = table_analysis_from_go_payload(
        go_payload,
        schema_name="demo",
        table_name="events",
        scan_at=py.scan_at,
        settings=Settings(cost_scan_tier="heuristic"),
    )
    assert mapped.total_files == py.total_files
    assert mapped.total_records == py.total_records
    assert mapped.small_files_count == py.small_files_count
    assert mapped.needs_compaction == py.needs_compaction
    assert mapped.compaction_priority == py.compaction_priority
    assert mapped.partition_col == py.partition_col
    assert mapped.total_partitions == py.total_partitions
    assert {p.partition_key: p.small_files_in_partition_count for p in mapped.partitions} == {
        p.partition_key: p.small_files_in_partition_count for p in py.partitions
    }


def test_go_payload_mapper_mor_deletes() -> None:
    payload = {
        "ok": True,
        "total_files": 3,
        "total_records": 10,
        "total_size_bytes": 100,
        "health_small_files": 3,
        "delete_files": 2,
        "equality_delete_files": 1,
        "position_delete_files": 1,
        "delete_bytes": 30,
        "manifest_count": 1,
        "manifest_total_bytes": 0,
        "total_snapshots": 1,
        "file_formats": ["PARQUET"],
        "partition_fields": [],
        "partitions": [
            {
                "partition_key": "_unpartitioned",
                "partition_value": "_unpartitioned",
                "partition_values": {},
                "files": 3,
                "small": 3,
                "size_bytes": 100,
            }
        ],
    }
    analysis = table_analysis_from_go_payload(
        payload,
        schema_name="demo",
        table_name="mor",
        scan_at=datetime(2026, 6, 30, tzinfo=UTC),
        settings=Settings(cost_scan_tier="heuristic"),
    )
    assert analysis.total_delete_files == 2
    assert analysis.raw["equality_delete_files"] == 1
    assert analysis.raw["position_delete_files"] == 1
    assert analysis.partition_col == "_unpartitioned"
    assert analysis.delete_file_pct == pytest.approx(66.67)


def test_go_payload_mapper_empty_table() -> None:
    analysis = table_analysis_from_go_payload(
        {
            "ok": True,
            "total_files": 0,
            "total_records": 0,
            "total_size_bytes": 0,
            "health_small_files": 0,
            "delete_files": 0,
            "equality_delete_files": 0,
            "position_delete_files": 0,
            "delete_bytes": 0,
            "manifest_count": 0,
            "manifest_total_bytes": 0,
            "total_snapshots": 0,
            "file_formats": [],
            "partition_fields": [],
            "partitions": [],
        },
        schema_name="demo",
        table_name="empty",
        scan_at=datetime(2026, 6, 30, tzinfo=UTC),
        settings=Settings(cost_scan_tier="heuristic"),
    )
    assert analysis.total_files == 0
    assert analysis.partitions == []
    assert analysis.needs_compaction is False


def test_python_scenarios_matrix() -> None:
    """Exercise partition/MOR/unpartitioned/oversize scenarios on the current scanner."""
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    settings = Settings(cost_scan_tier="heuristic")

    class UnpartitionedTable(FakeTable):
        def spec(self) -> Any:
            return SimpleNamespace(fields=[])

    empty_partition: tuple[str, ...] = ()
    unpartitioned = UnpartitionedTable(
        files=[
            FakeDataFile(10 * MB, 5, empty_partition),  # type: ignore[arg-type]
            FakeDataFile(20 * MB, 5, empty_partition),  # type: ignore[arg-type]
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
    )
    u = analyze_table(unpartitioned, "demo", "plain", scan_at=scan_at, settings=settings)
    assert u.partition_col == "_unpartitioned"
    assert u.partitions[0].partition_key == "_unpartitioned"

    mor = FakeTable(
        files=[
            FakeDataFile(50 * MB, 10, ("2026-06-29",)),
            FakeDataFile(1 * MB, 2, ("2026-06-29",), content="POSITION_DELETES"),
            FakeDataFile(2 * MB, 3, ("2026-06-29",), content="EQUALITY_DELETES"),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
    )
    m = analyze_table(mor, "demo", "mor", scan_at=scan_at, settings=settings)
    assert m.total_delete_files == 2
    assert m.raw["equality_delete_files"] == 1
    assert m.raw["position_delete_files"] == 1

    oversize = FakeTable(
        files=[FakeDataFile(512 * MB, 1000, ("2026-06-29",))],
        snapshots=[FakeSnapshotMeta(scan_at)],
    )
    o = analyze_table(oversize, "demo", "bigpart", scan_at=scan_at, settings=settings)
    assert o.total_partitions == 1
    assert o.partitions[0].compaction_status == "T"
    assert o.small_files_count == 0
