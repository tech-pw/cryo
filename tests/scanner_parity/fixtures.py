"""Configurable FakeTable builders + Go aggregate-fixture request serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cryo.config import Settings
from cryo.scanner.analyze import (
    _table_properties,
    table_location,
)
from cryo.scanner.analyze import (
    partition_fields as extract_partition_fields,
)
from cryo.scanner.commit_signals import commits_from_snapshots, commits_to_payload
from cryo.scanner.cost import _snapshot_timestamp
from cryo.scanner.signals import detect_format_version
from tests.test_scanner import (
    FakeCurrentSnapshot,
    FakeDataFile,
    FakeManifest,
    FakePartitionField,
    FakeSchema,
    FakeSnapshotMeta,
    FakeSourceColumn,
    FakeSpec,
)

MB = 1024 * 1024


@dataclass
class ParityColumn:
    field_id: int
    name: str
    field_type: str


@dataclass
class ParityPartitionField:
    source_id: int
    name: str
    transform: str


@dataclass
class ParityTable:
    """Flexible FakeTable stand-in for partition/transform/type matrix tests."""

    files: list[FakeDataFile]
    snapshots: list[FakeSnapshotMeta]
    columns: list[ParityColumn] = field(default_factory=list)
    partition_spec: list[ParityPartitionField] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    location: str | None = None
    current_snapshot_id: int | None = None
    io: object = field(default_factory=object)

    def __post_init__(self) -> None:
        if not self.columns:
            self.columns = [
                ParityColumn(1, "event_date", "date"),
                ParityColumn(2, "user_id", "string"),
            ]
        if not self.properties:
            self.properties = {
                "write.distribution-mode": "hash",
                "write.sort-order": "event_date",
                "write.target-file-size-bytes": "268435456",
                "format-version": "2",
            }
        props = dict(self.properties)
        snaps = list(self.snapshots)
        snap_id = self.current_snapshot_id

        class _Meta:
            properties = props
            snapshots = snaps
            current_snapshot_id = snap_id
            location = self.location

        self.metadata = _Meta()

    def spec(self) -> FakeSpec:
        return FakeSpec(
            [
                FakePartitionField(
                    source_id=f.source_id,
                    name=f.name,
                    transform=f.transform,
                )
                for f in self.partition_spec
            ]
        )

    def schema(self) -> FakeSchema:
        mapping = {c.field_id: FakeSourceColumn(c.name, c.field_type) for c in self.columns}

        class _Schema:
            def find_field(self, source_id: int) -> FakeSourceColumn:
                return mapping[source_id]

        return _Schema()  # type: ignore[return-value]

    def current_snapshot(self) -> FakeCurrentSnapshot:
        return FakeCurrentSnapshot(self.files)


def snapshot_iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_fixture_request(
    table: ParityTable,
    *,
    schema_name: str,
    table_name: str,
    settings: Settings,
) -> dict[str, Any]:
    """Serialize the same logical files Python analyzes into a Go aggregate-fixture request."""
    fields = extract_partition_fields(table)
    columns = _schema_columns(table)
    records: list[dict[str, Any]] = []
    for data_file in table.files:
        partition: dict[str, Any] = {}
        for index, part_field in enumerate(fields):
            name = str(part_field["partition_field"])
            part = data_file.partition
            partition[name] = part[index] if index < len(part) else None
        nulls = data_file.null_value_counts or {}
        vals = data_file.value_counts or {}
        records.append(
            {
                "size_bytes": data_file.file_size_in_bytes,
                "record_count": data_file.record_count,
                "content": data_file.content,
                "file_format": data_file.file_format,
                "spec_id": data_file.spec_id,
                "partition": partition,
                "null_value_counts": {str(k): int(v) for k, v in nulls.items()},
                "value_counts": {str(k): int(v) for k, v in vals.items()},
            }
        )

    snapshots = list(getattr(table.metadata, "snapshots", []) or [])
    committed = [ts for s in snapshots if (ts := _snapshot_timestamp(s)) is not None]
    first = min(committed) if committed else None
    last = max(committed) if committed else None
    format_version = detect_format_version(table)
    props = getattr(table.metadata, "properties", {}) or {}
    scan_properties = _table_properties(table)
    target = props.get("write.target-file-size-bytes")
    target_bytes = int(target) if target is not None and str(target).isdigit() else None
    write_mode = props.get("write.distribution-mode")
    has_sort = bool(props.get("write.sort-order"))
    current_id = getattr(table.metadata, "current_snapshot_id", None)
    if current_id is not None:
        current_id = int(current_id)

    # Match FakeManifest.manifest_length / single-manifest FakeCurrentSnapshot.
    manifest_count = 1 if hasattr(table, "current_snapshot") else 0
    manifest_total_bytes = FakeManifest.manifest_length if manifest_count else 0

    request: dict[str, Any] = {
        "schema_name": schema_name,
        "table_name": table_name,
        "small_file_mb": settings.scan_small_file_mb,
        "health_small_file_bytes": 8 * 1024 * 1024,
        "max_partitions": settings.scanner_max_partitions_per_table,
        "partition_fields": [
            {
                "base_column": f["base_column"],
                "partition_field": f["partition_field"],
                "transform": f["transform"],
                "transform_param": f["transform_param"],
                "col_type": f["col_type"],
            }
            for f in fields
        ],
        "records": records,
        "total_snapshots": len(snapshots),
        "commits": commits_to_payload(commits_from_snapshots(snapshots)),
        "manifest_count": manifest_count,
        "manifest_total_bytes": manifest_total_bytes,
        "write_distribution_mode": write_mode,
        "has_sort_order": has_sort,
        "target_file_size_bytes": target_bytes,
        "iceberg_properties": scan_properties["iceberg_properties"],
        "gc_enabled": scan_properties["gc_enabled"],
        "location": table_location(table) or "",
        "format_version": format_version,
        "columns": [
            {
                "field_id": c["field_id"],
                "column_name": c["column_name"],
                "column_type": c["column_type"],
            }
            for c in columns
        ],
    }
    if first is not None:
        request["first_snapshot_at"] = snapshot_iso(first)
    if last is not None:
        request["last_snapshot_committed_at"] = snapshot_iso(last)
    if current_id is not None:
        request["current_snapshot_id"] = current_id
    return request


def _schema_columns(table: object) -> list[dict[str, Any]]:
    if isinstance(table, ParityTable):
        return [
            {
                "field_id": c.field_id,
                "column_name": c.name,
                "column_type": c.field_type,
            }
            for c in table.columns
        ]
    # Default FakeSchema fields.
    return [
        {"field_id": 1, "column_name": "event_date", "column_type": "date"},
        {"field_id": 2, "column_name": "user_id", "column_type": "string"},
    ]


def day_iso_table(*, scan_at: datetime) -> ParityTable:
    return ParityTable(
        files=[
            FakeDataFile(10 * MB, 10, ("2026-06-29",)),
            FakeDataFile(20 * MB, 20, ("2026-06-29",)),
            FakeDataFile(30 * MB, 30, ("2026-06-29",)),
            FakeDataFile(256 * MB, 100, ("2026-06-30",)),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "event_date_day", "day")],
        columns=[
            ParityColumn(1, "event_date", "date"),
            ParityColumn(2, "user_id", "string"),
        ],
    )


def day_epoch_table(*, scan_at: datetime) -> ParityTable:
    # 2026-06-29 / 2026-06-30 as epoch days.
    return ParityTable(
        files=[
            FakeDataFile(10 * MB, 10, (20643,)),
            FakeDataFile(20 * MB, 20, (20643,)),
            FakeDataFile(5 * MB, 5, (20643,)),
            FakeDataFile(256 * MB, 100, (20644,)),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "event_date_day", "day")],
        columns=[ParityColumn(1, "event_date", "date")],
    )


def month_epoch_table(*, scan_at: datetime) -> ParityTable:
    return ParityTable(
        files=[
            FakeDataFile(15 * MB, 15, (678,)),
            FakeDataFile(15 * MB, 15, (678,)),
            FakeDataFile(200 * MB, 200, (679,)),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "event_date_month", "month")],
        columns=[ParityColumn(1, "event_date", "date")],
    )


def year_epoch_table(*, scan_at: datetime) -> ParityTable:
    return ParityTable(
        files=[
            FakeDataFile(40 * MB, 40, (56,)),
            FakeDataFile(40 * MB, 40, (56,)),
            FakeDataFile(40 * MB, 40, (56,)),
            FakeDataFile(300 * MB, 300, (57,)),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "event_date_year", "year")],
        columns=[ParityColumn(1, "event_date", "date")],
    )


def unpartitioned_table(*, scan_at: datetime) -> ParityTable:
    return ParityTable(
        files=[
            FakeDataFile(12 * MB, 12, ()),
            FakeDataFile(12 * MB, 12, ()),
            FakeDataFile(12 * MB, 12, ()),
            FakeDataFile(200 * MB, 200, ()),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[],
        columns=[
            ParityColumn(1, "id", "long"),
            ParityColumn(2, "payload", "string"),
        ],
        properties={
            "write.distribution-mode": "none",
            "write.target-file-size-bytes": "268435456",
            "format-version": "2",
        },
    )


def identity_string_table(*, scan_at: datetime) -> ParityTable:
    return ParityTable(
        files=[
            FakeDataFile(8 * MB, 8, ("acme",)),
            FakeDataFile(8 * MB, 8, ("acme",)),
            FakeDataFile(8 * MB, 8, ("acme",)),
            FakeDataFile(100 * MB, 100, ("globex",)),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "tenant_id", "identity")],
        columns=[ParityColumn(1, "tenant_id", "string")],
    )


def identity_date_table(*, scan_at: datetime) -> ParityTable:
    return ParityTable(
        files=[
            FakeDataFile(25 * MB, 25, ("2026-01-15",)),
            FakeDataFile(25 * MB, 25, ("2026-01-15",)),
            FakeDataFile(90 * MB, 90, ("2026-02-01",)),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "event_date", "identity")],
        columns=[ParityColumn(1, "event_date", "date")],
    )


def identity_timestamp_table(*, scan_at: datetime) -> ParityTable:
    ts1 = "2026-06-29T12:00:00+00:00"
    ts2 = "2026-06-30T18:30:00+00:00"
    return ParityTable(
        files=[
            FakeDataFile(22 * MB, 22, (ts1,)),
            FakeDataFile(22 * MB, 22, (ts1,)),
            FakeDataFile(120 * MB, 120, (ts2,)),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "event_ts", "identity")],
        columns=[ParityColumn(1, "event_ts", "timestamptz")],
    )


def multi_field_table(*, scan_at: datetime) -> ParityTable:
    return ParityTable(
        files=[
            FakeDataFile(10 * MB, 10, (20643, "acme")),
            FakeDataFile(10 * MB, 10, (20643, "acme")),
            FakeDataFile(10 * MB, 10, (20643, "acme")),
            FakeDataFile(80 * MB, 80, (20643, "globex")),
            FakeDataFile(256 * MB, 256, (20644, "acme")),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[
            ParityPartitionField(1, "event_date_day", "day"),
            ParityPartitionField(2, "tenant_id", "identity"),
        ],
        columns=[
            ParityColumn(1, "event_date", "date"),
            ParityColumn(2, "tenant_id", "string"),
        ],
    )


def bucket_table(*, scan_at: datetime) -> ParityTable:
    return ParityTable(
        files=[
            FakeDataFile(18 * MB, 18, (0,)),
            FakeDataFile(18 * MB, 18, (0,)),
            FakeDataFile(18 * MB, 18, (0,)),
            FakeDataFile(90 * MB, 90, (7,)),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "user_id_bucket", "bucket[16]")],
        columns=[ParityColumn(1, "user_id", "string")],
    )


def deletes_and_mixed_formats_table(*, scan_at: datetime) -> ParityTable:
    return ParityTable(
        files=[
            FakeDataFile(10 * MB, 10, (20643,), file_format="PARQUET"),
            FakeDataFile(10 * MB, 10, (20643,), file_format="PARQUET"),
            FakeDataFile(10 * MB, 10, (20643,), file_format="ORC"),
            FakeDataFile(
                1 * MB,
                1,
                (20643,),
                content="POSITION_DELETES",
                file_format="PARQUET",
            ),
            FakeDataFile(
                2 * MB,
                2,
                (20643,),
                content="EQUALITY_DELETES",
                file_format="PARQUET",
            ),
            FakeDataFile(200 * MB, 200, (20644,), file_format="PARQUET"),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "event_date_day", "day")],
        columns=[ParityColumn(1, "event_date", "date")],
        properties={
            "write.distribution-mode": "hash",
            "write.sort-order": "event_date",
            "write.target-file-size-bytes": "268435456",
            "format-version": "2",
        },
    )


def column_quality_table(*, scan_at: datetime) -> ParityTable:
    return ParityTable(
        files=[
            FakeDataFile(
                50 * MB,
                100,
                ("acme",),
                null_value_counts={1: 10, 2: 0},
                value_counts={1: 100, 2: 100},
            ),
            FakeDataFile(
                50 * MB,
                100,
                ("acme",),
                null_value_counts={1: 20, 2: 5},
                value_counts={1: 100, 2: 100},
            ),
            FakeDataFile(
                50 * MB,
                100,
                ("globex",),
                null_value_counts={1: 0, 2: 50},
                value_counts={1: 100, 2: 100},
            ),
        ],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "tenant_id", "identity")],
        columns=[
            ParityColumn(1, "tenant_id", "string"),
            ParityColumn(2, "email", "string"),
        ],
    )


def empty_table(*, scan_at: datetime) -> ParityTable:
    return ParityTable(
        files=[],
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "event_date_day", "day")],
        columns=[ParityColumn(1, "event_date", "date")],
    )


def high_cardinality_table(*, scan_at: datetime, n_parts: int = 8) -> ParityTable:
    files: list[FakeDataFile] = []
    for i in range(n_parts):
        # Vary small-file counts so cap ranking is deterministic.
        small_n = (i % 3) + 1
        for j in range(small_n):
            files.append(FakeDataFile((5 + j) * MB, 5 + j, (20000 + i,)))
        files.append(FakeDataFile((100 + i) * MB, 100 + i, (20000 + i,)))
    return ParityTable(
        files=files,
        snapshots=[FakeSnapshotMeta(scan_at)],
        partition_spec=[ParityPartitionField(1, "event_date_day", "day")],
        columns=[ParityColumn(1, "event_date", "date")],
    )
