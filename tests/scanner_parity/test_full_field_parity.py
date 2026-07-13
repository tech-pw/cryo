"""Full-field Python analyze_table ↔ Go aggregate-fixture parity (hard equality)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cryo.config import Settings
from cryo.scanner.analyze import analyze_table
from cryo.scanner.go_scan import run_aggregate_fixture, table_analysis_from_go_payload
from tests.scanner_parity.compare import assert_table_analysis_equal
from tests.scanner_parity.fixtures import (
    bucket_table,
    build_fixture_request,
    column_quality_table,
    day_epoch_table,
    day_iso_table,
    deletes_and_mixed_formats_table,
    empty_table,
    high_cardinality_table,
    identity_date_table,
    identity_string_table,
    identity_timestamp_table,
    month_epoch_table,
    multi_field_table,
    unpartitioned_table,
    year_epoch_table,
)

SCAN_AT = datetime(2026, 6, 30, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    base = {
        "cost_scan_tier": "heuristic",
        "scanner_max_partitions_per_table": 5000,
        "scan_small_file_mb": 89.6,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _disable_puffin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Puffin side-effects are Python-only; exclude from aggregate parity."""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("puffin disabled for parity tests")

    monkeypatch.setattr("cryo.scanner.puffin_writer.write_puffin_blob", _boom)


def _run_parity(table: Any, *, schema: str, name: str, settings: Settings | None = None) -> None:
    settings = settings or _settings()
    python = analyze_table(
        table,
        schema,
        name,
        scan_at=SCAN_AT,
        settings=settings,
    )
    request = build_fixture_request(table, schema_name=schema, table_name=name, settings=settings)
    payload = run_aggregate_fixture(request, settings=settings)
    go = table_analysis_from_go_payload(
        payload,
        schema_name=schema,
        table_name=name,
        scan_at=SCAN_AT,
        settings=settings,
    )
    assert_table_analysis_equal(python, go)


@pytest.mark.parametrize(
    ("builder", "schema", "name"),
    [
        (day_iso_table, "demo", "events_day_iso"),
        (day_epoch_table, "demo", "events_day_epoch"),
        (month_epoch_table, "demo", "events_month"),
        (year_epoch_table, "demo", "events_year"),
        (unpartitioned_table, "demo", "events_unpartitioned"),
        (identity_string_table, "demo", "events_identity_str"),
        (identity_date_table, "demo", "events_identity_date"),
        (identity_timestamp_table, "demo", "events_identity_ts"),
        (multi_field_table, "demo", "events_multi"),
        (bucket_table, "demo", "events_bucket"),
        (deletes_and_mixed_formats_table, "demo", "events_mor_mixed"),
        (column_quality_table, "demo", "events_quality"),
        (empty_table, "demo", "events_empty"),
    ],
)
def test_full_field_parity_matrix(builder: Any, schema: str, name: str) -> None:
    table = builder(scan_at=SCAN_AT - timedelta(days=1))
    _run_parity(table, schema=schema, name=name)


def test_full_field_parity_partition_cap() -> None:
    settings = _settings(scanner_max_partitions_per_table=3)
    table = high_cardinality_table(scan_at=SCAN_AT - timedelta(hours=6), n_parts=8)
    _run_parity(table, schema="demo", name="events_capped", settings=settings)


def test_full_field_parity_two_snapshots() -> None:
    from tests.scanner_parity.fixtures import ParityColumn, ParityPartitionField, ParityTable
    from tests.test_scanner import FakeDataFile, FakeSnapshotMeta

    MB = 1024 * 1024
    table = ParityTable(
        files=[
            FakeDataFile(10 * MB, 10, (20643,)),
            FakeDataFile(10 * MB, 10, (20643,)),
            FakeDataFile(10 * MB, 10, (20643,)),
        ],
        snapshots=[
            FakeSnapshotMeta(SCAN_AT - timedelta(days=10)),
            FakeSnapshotMeta(SCAN_AT - timedelta(days=1)),
        ],
        partition_spec=[ParityPartitionField(1, "event_date_day", "day")],
        columns=[ParityColumn(1, "event_date", "date")],
        current_snapshot_id=99,
    )
    _run_parity(table, schema="demo", name="events_two_snaps")
