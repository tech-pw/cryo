"""Parity + memory-path tests for the Iceberg metadata scanner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from cryo.config import Settings
from cryo.scanner.analyze import analyze_catalog_table, analyze_table, iter_data_files
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


def test_analyze_catalog_table_wires_a_working_table_obj_loader_for_go_engine() -> None:
    """Regression, 2026-07-31: analyze_catalog_table's Go branch used to hardcode
    table_obj=None, which is the actual call site that broke Tier-1 cost fleet-wide.
    Assert the loader it now builds really does call catalog.load_table(schema, table)
    -- not just that go_scan.py *can* accept one."""
    sentinel_table = object()
    catalog = SimpleNamespace(load_table=lambda schema, table: sentinel_table)
    captured: dict[str, Any] = {}

    def fake_analyze_catalog_table_go(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(cost=None)

    with patch(
        "cryo.scanner.go_scan.analyze_catalog_table_go",
        side_effect=fake_analyze_catalog_table_go,
    ):
        analyze_catalog_table(
            catalog,
            "demo",
            "events",
            settings=Settings(scan_engine="go"),
            source_config={"catalog": "sample"},
        )

    assert "table_obj_loader" in captured
    assert captured["table_obj_loader"]() is sentinel_table


def _go_payload_fixture(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "total_files": 4,
        "total_records": 40,
        "total_size_bytes": 40 * MB,
        "health_small_files": 0,
        "delete_files": 0,
        "equality_delete_files": 0,
        "position_delete_files": 0,
        "delete_bytes": 0,
        "manifest_count": 1,
        "manifest_total_bytes": 1024,
        "total_snapshots": 1,
        "file_formats": ["PARQUET"],
        "partition_fields": [],
        "partitions": [],
    }
    payload.update(overrides)
    return payload


def test_go_payload_lazily_loads_table_obj_when_eligible() -> None:
    """Regression, 2026-07-31: analyze_catalog_table_go used to be called with
    table_obj=None unconditionally, silently disabling Tier-1 cost accounting fleet-wide
    for every Go-scanned table -- table_cost/table_cost_history stopped getting new rows
    the day the Go engine became the primary scanner. A lazy loader lets Tier-1 cost run
    again once the (cheap, already-known) size/manifest thresholds pass."""
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = FakeTable(
        files=[FakeDataFile(10 * MB, 10, ("2026-06-29",)) for _ in range(4)],
        snapshots=[FakeSnapshotMeta(scan_at)],
    )
    calls = []

    def loader() -> Any:
        calls.append(1)
        return table

    analysis = table_analysis_from_go_payload(
        _go_payload_fixture(),
        schema_name="demo",
        table_name="events",
        scan_at=scan_at,
        settings=Settings(cost_scan_tier="metadata"),
        table_obj_loader=loader,
    )
    assert len(calls) == 1
    assert analysis.cost is not None


def test_go_payload_skips_table_obj_loader_when_file_count_too_high() -> None:
    """A huge table that will be threshold-filtered anyway must never pay for the
    catalog load -- that's the whole point of making this lazy instead of eager."""
    calls = []

    def loader() -> Any:
        calls.append(1)
        raise AssertionError("loader should not be called for an ineligible table")

    analysis = table_analysis_from_go_payload(
        _go_payload_fixture(total_files=10),
        schema_name="demo",
        table_name="events",
        scan_at=datetime(2026, 6, 30, tzinfo=UTC),
        settings=Settings(cost_scan_tier="metadata", cost_metadata_max_files_estimate=3),
        table_obj_loader=loader,
    )
    assert calls == []
    assert analysis.cost is None


def test_go_payload_falls_back_gracefully_when_loader_raises() -> None:
    """Tier-1 cost is an enhancement, not a requirement -- a catalog hiccup while loading
    the table handle must degrade to heuristic/no-cost, not fail the whole scan."""

    def loader() -> Any:
        raise RuntimeError("catalog unreachable")

    analysis = table_analysis_from_go_payload(
        _go_payload_fixture(),
        schema_name="demo",
        table_name="events",
        scan_at=datetime(2026, 6, 30, tzinfo=UTC),
        settings=Settings(cost_scan_tier="metadata"),
        table_obj_loader=loader,
    )
    assert analysis.cost is None
    assert analysis.total_files == 4


def test_go_payload_gets_cost_accounting_for_a_large_but_valuable_table() -> None:
    """Regression, 2026-08-03: cost_metadata_max_files_estimate's old default (200_000)
    excluded a real table with 207,511 files (24TB, ~$700/mo) from all cost/reclaim
    accounting -- table_cost had zero rows for it, ever. The real memory guard is inside
    account_metadata_cost itself (cost_scan_max_snapshots/max_manifests, checked live, with
    a graceful fallback either way), so the outer file-count pre-filter only needs to catch
    truly pathological cases, not tables like this one. Uses the new default directly (no
    settings override) so a future accidental regression of the default is caught too."""
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = FakeTable(
        files=[FakeDataFile(1 * MB, 1, ("2026-06-29",)) for _ in range(250_000)],
        snapshots=[FakeSnapshotMeta(scan_at)],
    )
    analysis = table_analysis_from_go_payload(
        _go_payload_fixture(total_files=250_000, manifest_count=9),
        schema_name="warehouse",
        table_name="gold_events",
        scan_at=scan_at,
        settings=Settings(cost_scan_tier="metadata"),
        table_obj_loader=lambda: table,
    )
    assert analysis.cost is not None
    from cryo.domain.enums import CostPrecision

    assert analysis.cost.precision is CostPrecision.METADATA


def test_go_payload_degrades_to_heuristic_not_nothing_when_snapshot_count_is_high() -> None:
    """A large-but-valuable table that also has many snapshots (past account_metadata_cost's
    own cost_scan_max_snapshots guard) must still get *some* real cost accounting --
    CostPrecision.HEURISTIC, not None -- exactly the graceful-degrade path this table
    (2,137 snapshots after a 2.5-day maintenance run) actually needs."""
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = FakeTable(
        files=[FakeDataFile(1 * MB, 1, ("2026-06-29",)) for _ in range(250_000)],
        snapshots=[FakeSnapshotMeta(scan_at) for _ in range(250)],
    )
    analysis = table_analysis_from_go_payload(
        _go_payload_fixture(total_files=250_000, manifest_count=9),
        schema_name="warehouse",
        table_name="gold_events",
        scan_at=scan_at,
        settings=Settings(cost_scan_tier="metadata", cost_scan_max_snapshots=200),
        table_obj_loader=lambda: table,
    )
    assert analysis.cost is not None
    from cryo.domain.enums import CostPrecision

    assert analysis.cost.precision is CostPrecision.HEURISTIC


def test_go_payload_ineligible_tier_never_calls_loader() -> None:
    calls = []

    def loader() -> Any:
        calls.append(1)
        return object()

    analysis = table_analysis_from_go_payload(
        _go_payload_fixture(),
        schema_name="demo",
        table_name="events",
        scan_at=datetime(2026, 6, 30, tzinfo=UTC),
        settings=Settings(cost_scan_tier="heuristic"),
        table_obj_loader=loader,
    )
    assert calls == []
    assert analysis.cost is None


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
    # partition_col is None, not the "_unpartitioned" display label -- see analyze.py's comment
    # on this field. A truthy partition_col gets treated by the planner as a real, bindable
    # column name, and the literal string "_unpartitioned" panics the Go engine's row-filter
    # binder when a job for this table's single pseudo-partition tries to bind it.
    assert analysis.partition_col is None
    assert analysis.delete_file_pct == pytest.approx(66.67)


def test_go_payload_mapper_clamps_delete_file_pct_when_deletes_outnumber_data_files() -> None:
    """Regression, 2026-07-28: total_files correctly excludes delete files as of the scan.go
    fix (a table's real data-file count, not inflated by tiny delete files landing under the
    small-file threshold). On a heavily MOR-fragmented table, delete files can genuinely
    outnumber data files -- delete_files / total_files must not report e.g. "428.7%"; it's
    clamped to 100, matching the pattern incremental.py already used for this same ratio."""
    payload = {
        "ok": True,
        "total_files": 2,
        "total_records": 10,
        "total_size_bytes": 100,
        "health_small_files": 2,
        "delete_files": 10,
        "equality_delete_files": 0,
        "position_delete_files": 10,
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
                "files": 2,
                "small": 2,
                "size_bytes": 100,
            }
        ],
    }
    analysis = table_analysis_from_go_payload(
        payload,
        schema_name="demo",
        table_name="mor_heavy",
        scan_at=datetime(2026, 6, 30, tzinfo=UTC),
        settings=Settings(cost_scan_tier="heuristic"),
    )
    assert analysis.total_delete_files == 10
    assert analysis.delete_file_pct == 100.0


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
    assert u.partition_col is None
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
