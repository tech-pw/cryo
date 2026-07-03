from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cryo.db.models import Table, TablePartition, TableStats, TableStatsHistory
from cryo.scanner.analyze import analyze_table
from cryo.scanner.cost import GB, estimate_storage_cost
from cryo.scanner.health import compute_health_score
from cryo.scanner.persistence import persist_table_analysis
from cryo.scanner.run import deterministic_chunk, parse_args

MB = 1024 * 1024


def test_health_score_uses_warn_and_critical_deductions() -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    stats = {
        "total_files": 100,
        "delete_file_pct": 0,
        "total_delete_files": 0,
        "total_snapshots": 55,
        "first_updated_at": now - timedelta(days=8),
        "manifest_count": 0,
        "total_partitions": 0,
        "partition_skew_ratio": 0,
        "avg_files_per_partition": 0,
        "write_distribution_mode": "hash",
        "has_sort_order": True,
        "target_file_size_bytes": 268_435_456,
        "file_formats": ["PARQUET"],
    }

    score, grade = compute_health_score(stats, health_small_file_count=25, now=now)

    assert score == 76
    assert grade == "B"


def test_storage_cost_includes_snapshot_overhead() -> None:
    estimate = estimate_storage_cost(
        total_size_bytes=100 * GB,
        total_delete_bytes=5 * GB,
        manifest_total_bytes=1 * GB,
        total_snapshots=50,
    )

    assert estimate.snapshot_overhead_gb == 15
    assert estimate.monthly_s3_cost_usd == 2.78


def test_deterministic_chunk_uses_full_sorted_position() -> None:
    assert deterministic_chunk(["a", "b", "c", "d", "e"], chunk_id=1, num_chunks=2) == [
        "b",
        "d",
    ]


def test_analyze_table_supports_fake_manifest_objects() -> None:
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

    analysis = analyze_table(table, "demo", "events", scan_at=scan_at)

    assert analysis.schema_name == "demo"
    assert analysis.table_name == "events"
    assert analysis.total_files == 4
    assert analysis.total_records == 160
    assert analysis.small_files_count == 3
    assert analysis.needs_compaction is True
    assert analysis.compaction_priority == "HIGH"
    assert analysis.partition_col == "event_date"
    assert analysis.partition_col_type == "date"
    assert analysis.partitions[0].partition_value == "2026-06-29"
    assert analysis.partitions[0].small_files_in_partition_count == 3
    assert analysis.partitions[0].compaction_status == "F"
    assert analysis.raw["partition_fields"][0]["transform"] == "day"


def test_persist_table_analysis_adds_latest_partitions_and_history() -> None:
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = Table(id=table_id, source_id=source_id, schema_name="demo", table_name="events")
    analysis = analyze_table(
        FakeTable(
            files=[
                FakeDataFile(10 * MB, 10, ("2026-06-29",)),
                FakeDataFile(20 * MB, 20, ("2026-06-29",)),
            ],
            snapshots=[FakeSnapshotMeta(scan_at)],
        ),
        "demo",
        "events",
        scan_at=scan_at,
    )
    session = FakeSession()

    persisted_id = persist_table_analysis(
        session, source_id=source_id, table=table, analysis=analysis
    )

    assert persisted_id == table_id
    assert any(isinstance(item, TableStats) for item in session.added)
    assert sum(isinstance(item, TablePartition) for item in session.added) == 1
    assert any(isinstance(item, TableStatsHistory) for item in session.added)
    stats = next(item for item in session.added if isinstance(item, TableStats))
    assert stats.total_files == 2
    assert stats.raw["partition_fields"][0]["base_column"] == "event_date"
    assert len(session.executed) == 3


def test_parse_args_accepts_cost_full_scan() -> None:
    args = parse_args(["--cost-full-scan", "--chunk-id", "1", "--num-chunks", "4"])
    assert args.cost_full_scan is True
    assert args.chunk_id == 1
    assert args.num_chunks == 4


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.executed: list[Any] = []

    def scalar(self, statement: Any) -> Any:
        del statement
        return None

    def get(self, model: type[Any], key: Any) -> Any:
        del model, key
        return None

    def add(self, item: Any) -> None:
        self.added.append(item)

    def add_all(self, items: list[Any]) -> None:
        self.added.extend(items)

    def flush(self) -> None:
        for item in self.added:
            if isinstance(item, Table) and item.id is None:
                item.id = uuid.uuid4()

    def execute(self, statement: Any) -> None:
        self.executed.append(statement)


@dataclass(frozen=True)
class FakeSourceColumn:
    name: str
    field_type: str


class FakeSchema:
    def find_field(self, source_id: int) -> FakeSourceColumn:
        assert source_id == 1
        return FakeSourceColumn("event_date", "date")


@dataclass(frozen=True)
class FakePartitionField:
    source_id: int = 1
    name: str = "event_date_day"
    transform: str = "day"


@dataclass(frozen=True)
class FakeSpec:
    fields: list[FakePartitionField]


@dataclass(frozen=True)
class FakeSnapshotMeta:
    committed_at: datetime


@dataclass(frozen=True)
class FakeDataFile:
    file_size_in_bytes: int
    record_count: int
    partition: tuple[str]
    content: str = "DATA"
    file_format: str = "PARQUET"
    spec_id: int = 0


@dataclass(frozen=True)
class FakeManifestEntry:
    data_file: FakeDataFile
    status: str = "ADDED"


class FakeManifest:
    manifest_length = 1024

    def __init__(self, files: list[FakeDataFile]) -> None:
        self.files = files

    def fetch_manifest_entry(self, io: object) -> list[FakeManifestEntry]:
        del io
        return [FakeManifestEntry(file) for file in self.files]


class FakeCurrentSnapshot:
    def __init__(self, files: list[FakeDataFile]) -> None:
        self.files = files

    def manifests(self, io: object) -> list[FakeManifest]:
        del io
        return [FakeManifest(self.files)]


class FakeMetadata:
    properties = {
        "write.distribution-mode": "hash",
        "write.sort-order": "event_date",
        "write.target-file-size-bytes": "268435456",
    }

    def __init__(self, snapshots: list[FakeSnapshotMeta]) -> None:
        self.snapshots = snapshots


class FakeTable:
    io = object()

    def __init__(self, files: list[FakeDataFile], snapshots: list[FakeSnapshotMeta]) -> None:
        self.files = files
        self.metadata = FakeMetadata(snapshots)

    def spec(self) -> FakeSpec:
        return FakeSpec([FakePartitionField()])

    def schema(self) -> FakeSchema:
        return FakeSchema()

    def current_snapshot(self) -> FakeCurrentSnapshot:
        return FakeCurrentSnapshot(self.files)
