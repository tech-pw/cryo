"""Partition raw.last_write_at.

Both scanners emit the same field, inside the existing partition raw JSON:

    last_write_at: UTC ISO-8601 with millisecond precision and a Z suffix
    YYYY-MM-DDTHH:MM:SS.mmmZ

Example: 1710491400123 -> 2024-03-15T08:30:00.123Z

The value is the max snapshot commit time of that partition's live manifest
entries. The key is omitted when no entry's snapshot timestamp can be resolved.
It is never the scan time, now(), or a placeholder epoch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryo.config import Settings
from cryo.scanner.analyze import analyze_table
from cryo.scanner.go_scan import _partition_from_go

EXAMPLE_MS = 1_710_491_400_123
EXAMPLE = "2024-03-15T08:30:00.123Z"
OLDER = "2024-01-01T00:00:00.000Z"
OLDER_MS = 1_704_067_200_000


@dataclass
class _File:
    file_size_in_bytes: int
    record_count: int
    partition: tuple[Any, ...]
    content: str = "DATA"
    file_format: str = "PARQUET"
    spec_id: int = 0


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


class _CurrentSnapshot:
    def __init__(self, entries: list[_Entry]) -> None:
        self.entries = entries

    def manifests(self, io: object) -> list[_Manifest]:
        del io
        return [_Manifest(self.entries)]


@dataclass
class _Snap:
    snapshot_id: int
    timestamp_ms: int | None = None
    committed_at: datetime | None = None


class _Metadata:
    properties: dict[str, str] = {}

    def __init__(self, snapshots: list[_Snap]) -> None:
        self.snapshots = snapshots


class _Schema:
    def find_field(self, source_id: int) -> object:
        del source_id
        return type("Col", (), {"name": "event_date", "field_type": "date"})()


class _Table:
    io = object()

    def __init__(self, entries: list[_Entry], snapshots: list[_Snap]) -> None:
        self.metadata = _Metadata(snapshots)
        self._entries = entries

    def spec(self) -> object:
        field = type("Field", (), {"source_id": 1, "name": "event_date_day", "transform": "day"})()
        return type("Spec", (), {"fields": [field]})()

    def schema(self) -> _Schema:
        return _Schema()

    def current_snapshot(self) -> _CurrentSnapshot:
        return _CurrentSnapshot(self._entries)


def _analyze(entries: list[_Entry], snapshots: list[_Snap]) -> dict[str, dict[str, Any]]:
    scan_at = datetime(2099, 1, 1, tzinfo=UTC)
    analysis = analyze_table(
        _Table(entries, snapshots),
        "demo",
        "events",
        scan_at=scan_at,
        settings=Settings(cost_scan_tier="heuristic"),
        parallel_manifests=False,
    )
    return {row.partition_key: row.raw for row in analysis.partitions}


def test_last_write_at_is_max_snapshot_timestamp_iso8601() -> None:
    raw = _analyze(
        [
            _Entry(_File(10, 1, ("2024-03-01",)), snapshot_id=1),
            _Entry(_File(10, 1, ("2024-03-01",), content="POSITION_DELETES"), snapshot_id=2),
            _Entry(_File(10, 1, ("2024-03-02",)), snapshot_id=1),
            _Entry(_File(10, 1, ("2024-03-01",)), snapshot_id=99),
        ],
        [_Snap(1, OLDER_MS), _Snap(2, EXAMPLE_MS)],
    )
    assert raw["event_date_day=2024-03-01"]["last_write_at"] == EXAMPLE
    assert raw["event_date_day=2024-03-02"]["last_write_at"] == OLDER
    parsed = datetime.fromisoformat(EXAMPLE.replace("Z", "+00:00"))
    assert parsed == datetime(2024, 3, 15, 8, 30, 0, 123000, tzinfo=UTC)


def test_last_write_at_omitted_when_snapshot_timestamp_unavailable() -> None:
    raw = _analyze(
        [
            _Entry(_File(10, 1, ("2024-03-01",))),
            _Entry(_File(10, 1, ("2024-03-02",)), snapshot_id=7),
        ],
        [_Snap(1, committed_at=datetime(2024, 3, 15, 8, 30, 0, 123000, tzinfo=UTC))],
    )
    assert "last_write_at" not in raw["event_date_day=2024-03-01"]
    assert raw["event_date_day=2024-03-01"]["delete_files"] == 0


def test_expired_snapshot_falls_back_to_oldest_retained_snapshot() -> None:
    raw = _analyze(
        [_Entry(_File(10, 1, ("2024-03-02",)), snapshot_id=7)],
        [_Snap(1, OLDER_MS), _Snap(2, EXAMPLE_MS)],
    )
    # Snapshot 7 was expired: the file predates the oldest retained snapshot.
    assert raw["event_date_day=2024-03-02"]["last_write_at"] == OLDER


def test_last_write_at_uses_committed_at_when_timestamp_ms_absent() -> None:
    raw = _analyze(
        [_Entry(_File(10, 1, ("2024-03-01",)), snapshot_id=1)],
        [_Snap(1, committed_at=datetime(2024, 3, 15, 8, 30, 0, 123000, tzinfo=UTC))],
    )
    assert raw["event_date_day=2024-03-01"]["last_write_at"] == EXAMPLE


def test_go_payload_copies_last_write_at_into_partition_raw() -> None:
    present = _partition_from_go(
        {
            "partition_key": "event_date_day=2024-03-01",
            "partition_value": "2024-03-01",
            "partition_values": {"event_date_day": "2024-03-01"},
            "files": 1,
            "small": 0,
            "size_bytes": 10,
            "delete_files": 0,
            "last_write_at": EXAMPLE,
        },
        [],
    )
    assert present.raw["last_write_at"] == EXAMPLE
    assert present.raw["partition_values"] == {"event_date_day": "2024-03-01"}

    for missing in (None, "", 1710491400123):
        row = _partition_from_go(
            {
                "partition_key": "_unpartitioned",
                "files": 1,
                "last_write_at": missing,
            },
            [],
        )
        assert "last_write_at" not in row.raw
