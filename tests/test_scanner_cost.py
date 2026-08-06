from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryo.config import Settings
from cryo.domain.enums import CostPrecision
from cryo.scanner.cost import account_metadata_cost


class FakeSnapshot:
    def __init__(self, snapshot_id: int, timestamp_ms: int, files: list[FakeDataFile]) -> None:
        self.snapshot_id = snapshot_id
        self.timestamp_ms = timestamp_ms
        self._files = files

    def manifests(self, io: object) -> list[FakeManifest]:
        del io
        return [FakeManifest(self._files)]


@dataclass(frozen=True)
class FakeDataFile:
    file_path: str
    file_size_in_bytes: int
    content: str = "DATA"


@dataclass(frozen=True)
class FakeManifestEntry:
    data_file: FakeDataFile
    status: str = "ADDED"


class FakeManifest:
    manifest_length = 512

    def __init__(self, files: list[FakeDataFile], manifest_path: str | None = None) -> None:
        self.files = files
        self.manifest_path = manifest_path or f"s3://bucket/metadata/{id(self)}.avro"

    def fetch_manifest_entry(self, io: object) -> list[FakeManifestEntry]:
        del io
        return [FakeManifestEntry(file) for file in self.files]


class FakeTable:
    io = object()

    def __init__(
        self,
        snapshots: list[FakeSnapshot],
        files_by_snapshot: dict[int, list[FakeDataFile]],
    ):
        del files_by_snapshot
        self._snapshots = snapshots
        self.metadata = type("Meta", (), {"snapshots": snapshots})()

    def snapshots(self) -> list[FakeSnapshot]:
        return self._snapshots

    def current_snapshot(self) -> FakeSnapshot:
        return self._snapshots[-1]

    def snapshot(self, snapshot_id: int) -> FakeSnapshot:
        for snapshot in self._snapshots:
            if snapshot.snapshot_id == snapshot_id:
                return snapshot
        raise KeyError(snapshot_id)


def test_metadata_accounting_reclaims_only_expired_files() -> None:
    scan_at = datetime(2026, 7, 3, tzinfo=UTC)
    old_ts = int((scan_at - timedelta(days=30)).timestamp() * 1000)
    recent_ts = int((scan_at - timedelta(days=1)).timestamp() * 1000)
    snapshots = [
        FakeSnapshot(
            snapshot_id=1,
            timestamp_ms=old_ts,
            files=[FakeDataFile("s3://bucket/data/old.parquet", 1000)],
        ),
        FakeSnapshot(
            snapshot_id=2,
            timestamp_ms=recent_ts,
            files=[
                FakeDataFile("s3://bucket/data/live.parquet", 2000),
                FakeDataFile("s3://bucket/data/delete.parquet", 300, content="DELETES"),
            ],
        ),
    ]
    table = FakeTable(snapshots, {})

    result = account_metadata_cost(table, scan_at=scan_at, settings=Settings())

    assert result.precision is CostPrecision.METADATA
    assert result.breakdown.live_bytes == 2000
    assert result.breakdown.delete_bytes == 300
    assert result.breakdown.dead_bytes == 1000
    assert result.reclaim_expire_bytes == 1000
    assert result.reclaim_optimize_bytes == 300
    assert "s3://bucket/data/live.parquet" in result.referenced_file_paths


def test_metadata_accounting_falls_back_when_snapshot_bound_exceeded() -> None:
    scan_at = datetime(2026, 7, 3, tzinfo=UTC)
    snapshots = [
        FakeSnapshot(snapshot_id=i, timestamp_ms=int(scan_at.timestamp() * 1000) - i, files=[])
        for i in range(5)
    ]
    table = FakeTable(snapshots, {})
    settings = Settings(cost_scan_max_snapshots=2)

    result = account_metadata_cost(table, scan_at=scan_at, settings=settings)

    assert result.precision is CostPrecision.HEURISTIC
    assert result.raw["bounded_out"] == "snapshots"


def test_heuristic_fallback_still_populates_referenced_file_paths() -> None:
    """Regression, 2026-08-06: the heuristic fallback (tables over cost_scan_max_snapshots,
    e.g. any table with real production churn -- 50 of 54 tables fleet-wide with >200
    snapshots hit this) used to hardcode referenced_file_paths={} even though it walks the
    current snapshot's manifests anyway to compute live_bytes/delete_bytes. Downstream,
    scan_orphans() treats "not in the referenced set" as "orphan, safe to delete" -- an
    empty set there doesn't mean no files are referenced, it means the fallback never
    looked. Confirmed live: one 24TB table reported 64.6TB "reclaimable" (2.7x its own
    size) purely from this gap. The fallback must not lose the file paths it already has
    in hand while computing live_bytes.
    """
    scan_at = datetime(2026, 7, 3, tzinfo=UTC)
    snapshots = [
        FakeSnapshot(snapshot_id=i, timestamp_ms=int(scan_at.timestamp() * 1000) - i, files=[])
        for i in range(4)
    ]
    snapshots.append(
        FakeSnapshot(
            snapshot_id=99,
            timestamp_ms=int(scan_at.timestamp() * 1000),
            files=[
                FakeDataFile("s3://bucket/data/current-live.parquet", 5000),
                FakeDataFile("s3://bucket/data/current-delete.parquet", 700, content="DELETES"),
            ],
        )
    )
    table = FakeTable(snapshots, {})
    settings = Settings(cost_scan_max_snapshots=2)

    result = account_metadata_cost(table, scan_at=scan_at, settings=settings)

    assert result.precision is CostPrecision.HEURISTIC
    assert result.raw["bounded_out"] == "snapshots"
    # The real bug: this used to be {} unconditionally.
    assert result.referenced_file_paths == {
        "s3://bucket/data/current-live.parquet": 5000,
        "s3://bucket/data/current-delete.parquet": 700,
    }
    # live/delete accounting was already correct before this fix -- must stay correct.
    assert result.breakdown.live_bytes == 5000
    assert result.breakdown.delete_bytes == 700


def test_heuristic_fallback_with_no_current_snapshot_files_stays_empty() -> None:
    """No files in the current snapshot is a legitimately empty referenced set --
    must not be conflated with "we didn't look" (the bug) vs "there's nothing here"
    (a real, if unusual, state)."""
    scan_at = datetime(2026, 7, 3, tzinfo=UTC)
    snapshots = [
        FakeSnapshot(snapshot_id=i, timestamp_ms=int(scan_at.timestamp() * 1000) - i, files=[])
        for i in range(5)
    ]
    table = FakeTable(snapshots, {})
    settings = Settings(cost_scan_max_snapshots=2)

    result = account_metadata_cost(table, scan_at=scan_at, settings=settings)

    assert result.referenced_file_paths == {}
    assert result.breakdown.live_bytes == 0
