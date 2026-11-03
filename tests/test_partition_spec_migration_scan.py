"""Scanners bucket files written before a partition spec change by their own spec (#289).

Before this, both scanners read every file's partition tuple with the default spec's
fields, so an older spec's partitions were mislabelled (Python, by position) or collapsed
into one null partition (Go, by field id), and nothing said which spec was current.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryo.config import Settings
from cryo.scanner.analyze import analyze_table, older_spec_fields
from cryo.scanner.go_scan import table_analysis_from_go_payload

MB = 1024 * 1024


@dataclass
class _File:
    file_size_in_bytes: int
    partition: tuple[Any, ...]
    spec_id: int
    record_count: int = 10
    content: str = "DATA"
    file_format: str = "PARQUET"


@dataclass
class _Entry:
    data_file: _File
    status: str = "ADDED"
    snapshot_id: int | None = None


class _Manifest:
    manifest_length = 128

    def __init__(self, entries: list[_Entry]) -> None:
        self.entries = entries

    def fetch_manifest_entry(self, io: object) -> list[_Entry]:
        del io
        return list(self.entries)


class _Snapshot:
    def __init__(self, entries: list[_Entry]) -> None:
        self.entries = entries

    def manifests(self, io: object) -> list[_Manifest]:
        del io
        return [_Manifest(self.entries)]


class _Metadata:
    properties: dict[str, str] = {}
    snapshots: list[object] = []


class _Schema:
    def find_field(self, source_id: int) -> object:
        del source_id
        return type("Col", (), {"name": "ts", "field_type": "timestamp"})()


def _spec(spec_id: int, name: str, transform: str) -> object:
    field = type("Field", (), {"source_id": 1, "name": name, "transform": transform})()
    return type("Spec", (), {"spec_id": spec_id, "fields": [field]})()


class _EvolvedTable:
    """day(ts) (spec 0) changed to month(ts) (spec 1)."""

    io = object()

    def __init__(self, entries: list[_Entry]) -> None:
        self.metadata = _Metadata()
        self._entries = entries

    def spec(self) -> object:
        return _spec(1, "ts_month", "month")

    def specs(self) -> dict[int, object]:
        return {0: _spec(0, "ts_day", "day"), 1: self.spec()}

    def schema(self) -> _Schema:
        return _Schema()

    def current_snapshot(self) -> _Snapshot:
        return _Snapshot(self._entries)


def _analyze(entries: list[_Entry]) -> Any:
    return analyze_table(
        _EvolvedTable(entries),
        "demo",
        "events",
        scan_at=datetime(2099, 1, 1, tzinfo=UTC),
        settings=Settings(cost_scan_tier="heuristic"),
        parallel_manifests=False,
    )


def test_older_spec_fields_excludes_the_default_spec() -> None:
    fields = older_spec_fields(_EvolvedTable([]))

    assert list(fields) == [0]
    assert fields[0][0]["partition_field"] == "ts_day"
    assert fields[0][0]["transform"] == "day"


def test_python_scanner_keys_older_spec_files_by_their_own_spec() -> None:
    analysis = _analyze(
        [
            _Entry(_File(200 * MB, (20000,), spec_id=0)),
            _Entry(_File(210 * MB, (20000,), spec_id=0)),
            _Entry(_File(220 * MB, (20001,), spec_id=0)),
            _Entry(_File(5 * MB, (657,), spec_id=1)),
        ]
    )

    rows = {row.partition_key: row for row in analysis.partitions}
    assert set(rows) == {"spec=0/ts_day=20000", "spec=0/ts_day=20001", "ts_month=657"}
    old = rows["spec=0/ts_day=20000"]
    assert old.partition_spec_id == 0
    assert old.partition_values == {"ts_day": 20000}
    assert old.partition_fields[0]["partition_field"] == "ts_day"
    assert old.files_per_partition == 2
    current = rows["ts_month=657"]
    assert current.partition_spec_id == 1
    assert current.partition_fields[0]["partition_field"] == "ts_month"
    assert analysis.raw["spec_migration"] == {
        "default_spec_id": 1,
        "old_spec_partitions": 2,
        "old_spec_files": 3,
        "old_spec_bytes": 630 * MB,
    }


def test_python_scanner_without_spec_ids_keeps_single_spec_keys() -> None:
    analysis = _analyze([_Entry(_File(5 * MB, (657,), spec_id=1))])

    assert [row.partition_key for row in analysis.partitions] == ["ts_month=657"]
    assert analysis.raw["spec_migration"]["old_spec_partitions"] == 0


def _go_payload() -> dict[str, Any]:
    month = {
        "base_column": "ts",
        "partition_field": "ts_month",
        "transform": "month",
        "col_type": "timestamp",
        "field_id": 1001,
    }
    day = {**month, "partition_field": "ts_day", "transform": "day", "field_id": 1000}
    return {
        "ok": True,
        "total_files": 3,
        "total_size_bytes": 425 * MB,
        "partition_fields": [month],
        "default_spec_id": 1,
        "old_spec_partitions": 1,
        "old_spec_files": 2,
        "old_spec_bytes": 420 * MB,
        "partitions": [
            {
                "partition_key": "spec=0/ts_day=20000",
                "partition_value": "20000",
                "partition_values": {"ts_day": 20000},
                "partition_spec_id": 0,
                "partition_fields": [day],
                "files": 2,
                "small": 0,
                "size_bytes": 420 * MB,
            },
            {
                "partition_key": "ts_month=657",
                "partition_value": "657",
                "partition_values": {"ts_month": 657},
                "partition_spec_id": 1,
                "files": 1,
                "small": 1,
                "size_bytes": 5 * MB,
            },
        ],
    }


def test_go_payload_maps_older_spec_rows_and_totals() -> None:
    analysis = table_analysis_from_go_payload(
        _go_payload(),
        schema_name="demo",
        table_name="events",
        scan_at=datetime(2099, 1, 1, tzinfo=UTC),
        settings=Settings(cost_scan_tier="heuristic"),
    )

    rows = {row.partition_key: row for row in analysis.partitions}
    old = rows["spec=0/ts_day=20000"]
    assert old.partition_fields == [
        {
            "base_column": "ts",
            "partition_field": "ts_day",
            "transform": "day",
            "transform_param": None,
            "col_type": "timestamp",
        }
    ]
    assert rows["ts_month=657"].partition_fields[0]["partition_field"] == "ts_month"
    assert analysis.raw["spec_migration"] == {
        "default_spec_id": 1,
        "old_spec_partitions": 1,
        "old_spec_files": 2,
        "old_spec_bytes": 420 * MB,
    }


def test_go_payload_older_unpartitioned_spec_keeps_no_fields() -> None:
    payload = _go_payload()
    payload["partitions"].append(
        {
            "partition_key": "spec=2/",
            "partition_value": "",
            "partition_values": {},
            "partition_spec_id": 2,
            "files": 4,
            "small": 4,
            "size_bytes": 8 * MB,
        }
    )
    analysis = table_analysis_from_go_payload(
        payload,
        schema_name="demo",
        table_name="events",
        scan_at=datetime(2099, 1, 1, tzinfo=UTC),
        settings=Settings(cost_scan_tier="heuristic"),
    )

    rows = {row.partition_key: row for row in analysis.partitions}
    assert rows["spec=2/"].partition_fields == []
