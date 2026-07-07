from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from cryo.db.base import Base
from cryo.db.models import Source, Table, TablePartition, TableStats, TableStatsHistory
from cryo.scanner.analyze import analyze_table
from cryo.scanner.cost import GB, estimate_storage_cost
from cryo.scanner.health import compute_health_score
from cryo.scanner.persistence import persist_table_analysis, record_table_scan_outcome
from cryo.scanner.run import deterministic_chunk, parse_args
from tests.test_presets_api_db import (
    _assign_missing_uuid_primary_keys,
    _patch_sqlite_incompatible_defaults,
)

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

    score, grade, deductions = compute_health_score(stats, health_small_file_count=25, now=now)

    assert score == 76
    assert grade == "B"
    assert sum(d["deduction"] for d in deductions) == 100 - score
    assert {d["label"] for d in deductions} == {
        "Small-file fragmentation",
        "Snapshot retention buildup",
        "Stale table commits",
    }


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


def test_analyze_table_computes_zero_scan_column_quality() -> None:
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = FakeTable(
        files=[
            FakeDataFile(
                10 * MB,
                10,
                ("2026-06-29",),
                null_value_counts={2: 0},
                value_counts={2: 50},
            ),
            FakeDataFile(
                20 * MB,
                20,
                ("2026-06-29",),
                null_value_counts={2: 10},
                value_counts={2: 50},
            ),
        ],
        snapshots=[FakeSnapshotMeta(scan_at - timedelta(days=1))],
    )

    analysis = analyze_table(table, "demo", "events", scan_at=scan_at)

    assert analysis.raw["avg_null_rate"] == 0.1
    assert analysis.raw["max_null_rate"] == 0.1
    assert analysis.raw["worst_null_column"] == "user_id"
    [column] = analysis.raw["column_quality"]
    assert column["column_name"] == "user_id"
    assert column["null_count"] == 10
    assert column["value_count"] == 100


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


def test_json_safe_strips_nul_bytes_postgres_cannot_store() -> None:
    """A dirty VARCHAR min/max bound decoded straight from manifest stats can legally contain a
    NUL byte -- Postgres text/JSONB columns can never store one, and would fail the whole row's
    INSERT rather than just that one field (see the psycopg UntranslatableCharacter error this
    guards against)."""
    from cryo.scanner.persistence import _json_safe

    assert _json_safe("clean") == "clean"
    assert _json_safe("dirty\x00value") == "dirtyvalue"
    assert _json_safe({"lower_bound": "\x00leading", "count": 3}) == {
        "lower_bound": "leading",
        "count": 3,
    }
    assert _json_safe(["a\x00b", ("c\x00d",)]) == ["ab", ["cd"]]


def test_record_table_scan_outcome_tracks_and_resets_consecutive_failures() -> None:
    """The whole point of tracking this: a table failing once (a network blip) shouldn't be
    flagged as broken, only a run of repeated failures -- and any later success must clear the
    streak entirely, not just decrement it."""
    _patch_sqlite_incompatible_defaults()
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS cryo"))
        Base.metadata.create_all(connection, tables=[Source.__table__, Table.__table__])
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    event.listen(session_factory, "before_flush", _assign_missing_uuid_primary_keys)

    source_id = uuid.uuid4()
    with session_factory() as session:
        session.add(Source(id=source_id, name="demo", type="rest", config={}))
        session.commit()

    scan_at = datetime(2026, 1, 1, tzinfo=UTC)
    kwargs = {"source_id": source_id, "schema_name": "sch", "table_name": "gold_dbt_tmp"}

    with session_factory() as session:
        table = record_table_scan_outcome(session, **kwargs, error=None, scanned_at=scan_at)
        session.commit()
        assert table.consecutive_scan_failures == 0

    with session_factory() as session:
        table = record_table_scan_outcome(session, **kwargs, error="boom", scanned_at=scan_at)
        session.commit()
        assert table.consecutive_scan_failures == 1
        assert table.last_scan_error == "boom"

    with session_factory() as session:
        table = record_table_scan_outcome(session, **kwargs, error="boom again", scanned_at=scan_at)
        session.commit()
        assert table.consecutive_scan_failures == 2
        assert table.last_scan_error == "boom again"

    with session_factory() as session:
        table = record_table_scan_outcome(session, **kwargs, error=None, scanned_at=scan_at)
        session.commit()
        assert table.consecutive_scan_failures == 0
        assert table.last_scan_error is None
        assert table.last_scan_error_at is None


def test_snapshot_info_tracks_first_and_last_commit() -> None:
    from cryo.scanner.analyze import _snapshot_info

    first = datetime(2026, 1, 1, tzinfo=UTC)
    latest = datetime(2026, 6, 29, tzinfo=UTC)
    table = FakeTable(
        files=[],
        snapshots=[FakeSnapshotMeta(first), FakeSnapshotMeta(latest)],
    )

    info = _snapshot_info(table)

    assert info["first_snapshot_at"] == first
    assert info["last_snapshot_committed_at"] == latest
    assert info["total_snapshots"] == 2


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
    _fields = {
        1: FakeSourceColumn("event_date", "date"),
        2: FakeSourceColumn("user_id", "string"),
    }

    def find_field(self, source_id: int) -> FakeSourceColumn:
        return self._fields[source_id]


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
    null_value_counts: dict[int, int] | None = None
    value_counts: dict[int, int] | None = None


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
