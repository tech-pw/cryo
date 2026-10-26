"""Commit events keep scan_at honest (issue #188); one small-file definition and a sane
metadata ratio for empty tables (issue #191)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from cryo.events.consumer import TableCommitConsumer
from cryo.events.watcher import InMemorySnapshotProbe, TableCommitted, TableCommitWatcher
from cryo.scanner.analyze import analyze_table
from cryo.scanner.health import compute_health_score
from cryo.scanner.incremental import CommitDelta, apply_commit_delta
from cryo.scanner.signals import enrich_stats_signals, metadata_to_data_ratio
from tests.test_scanner import FakeDataFile, FakeSnapshotMeta, FakeTable

MB = 1024 * 1024
SCANNED_AT = datetime(2026, 7, 1, tzinfo=UTC)


class _FakeSession:
    def __init__(self, stats: object) -> None:
        self._stats = stats

    def get(self, model: object, key: object) -> object:
        del model, key
        return self._stats


def _stats(table_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        table_id=table_id,
        snapshot_watermark=10,
        scan_at=SCANNED_AT,
        total_files=100,
        total_size_gb=10.0,
        total_delete_files=10,
        equality_delete_files=4,
        position_delete_files=6,
        delete_file_pct=10.0,
        query_count_30d=0,
        small_files_count=0,
        total_snapshots=40,
        raw={},
        detection_tier=None,
    )


def _delta(table_id: uuid.UUID, snapshot_id: int, **kwargs: int) -> CommitDelta:
    fields = {
        "files_added": 0,
        "files_removed": 0,
        "bytes_added": 0,
        "bytes_removed": 0,
        "delete_files_added": 0,
    }
    fields.update(kwargs)
    return CommitDelta(
        table_id=table_id,
        snapshot_id=snapshot_id,
        parent_snapshot_id=snapshot_id - 1,
        committed_at=SCANNED_AT + timedelta(days=3),
        **fields,
    )


# --- #188 -------------------------------------------------------------------------------


def test_watermark_only_commit_keeps_scan_at() -> None:
    table_id = uuid.uuid4()
    stats = _stats(table_id)
    later = SCANNED_AT + timedelta(days=3, minutes=1)

    assert apply_commit_delta(_FakeSession(stats), _delta(table_id, 11), now=later) is True

    assert stats.scan_at == SCANNED_AT
    assert stats.snapshot_watermark == 11
    assert stats.last_snapshot_committed_at == SCANNED_AT + timedelta(days=3)
    assert stats.raw["commits_since_scan"] == 1
    assert stats.raw["watermark_only_commits_since_scan"] == 1


def test_freshness_still_sees_the_previous_scan_after_many_commits() -> None:
    table_id = uuid.uuid4()
    stats = _stats(table_id)
    for offset, snapshot_id in enumerate(range(11, 16)):
        now = SCANNED_AT + timedelta(days=3, minutes=offset)
        apply_commit_delta(_FakeSession(stats), _delta(table_id, snapshot_id), now=now)

    max_stats_age = timedelta(days=1)
    assert SCANNED_AT + timedelta(days=3) - stats.scan_at > max_stats_age
    assert stats.raw["commits_since_scan"] == 5


def test_delete_file_removals_bring_delete_pct_down() -> None:
    table_id = uuid.uuid4()
    stats = _stats(table_id)
    delta = _delta(
        table_id,
        11,
        delete_files_removed=8,
        equality_delete_files_removed=4,
        position_delete_files_removed=4,
        snapshots_added=1,
    )

    apply_commit_delta(_FakeSession(stats), delta, now=SCANNED_AT + timedelta(days=1))

    assert stats.total_delete_files == 2
    assert stats.equality_delete_files == 0
    assert stats.position_delete_files == 2
    assert stats.delete_file_pct == 2.0
    assert stats.total_snapshots == 41
    assert "watermark_only_commits_since_scan" not in stats.raw


def test_removal_fields_round_trip_through_the_event_stream() -> None:
    published: list[dict] = []

    class _Stream:
        def publish(self, stream: str, payload: dict) -> str:
            del stream
            published.append(payload)
            return "1-0"

    consumer = TableCommitConsumer(_Stream())  # type: ignore[arg-type]
    consumer.publish(
        TableCommitted(
            table_ref="iceberg.db.t",
            snapshot_id=5,
            observed_at=SCANNED_AT,
            delete_files_removed=3,
            position_delete_files_removed=2,
            snapshots_added=1,
        )
    )
    assert published[0]["delete_files_removed"] == 3
    assert published[0]["position_delete_files_removed"] == 2
    assert published[0]["snapshots_added"] == 1


def test_watcher_reports_the_parent_snapshot() -> None:
    probe = InMemorySnapshotProbe({"iceberg.db.t": 1})
    events: list[TableCommitted] = []
    watcher = TableCommitWatcher(probe, events.append, hot_table_refs={"iceberg.db.t"})
    watcher.poll_once()
    probe.snapshots["iceberg.db.t"] = 2
    watcher.poll_once()
    assert [(e.snapshot_id, e.parent_snapshot_id) for e in events] == [(2, 1)]


# --- #191 -------------------------------------------------------------------------------


def _table_with(files: list[FakeDataFile]) -> FakeTable:
    return FakeTable(files=files, snapshots=[FakeSnapshotMeta(SCANNED_AT - timedelta(days=1))])


def _small_file_deductions(analysis: object) -> list[dict]:
    return [
        d
        for d in analysis.raw["health_deductions"]  # type: ignore[attr-defined]
        if d["label"] == "Small-file fragmentation"
    ]


def test_one_small_file_per_partition_is_not_scored_as_fragmentation() -> None:
    # 200 partitions each holding one tiny file and one big file: nothing the planner could
    # compact, so the grade must not blame small files the count says aren't there.
    files = []
    for day in range(200):
        partition = (f"2026-01-{day:03d}",)
        files.append(FakeDataFile(1 * MB, 1, partition))
        files.append(FakeDataFile(256 * MB, 100, partition))
    analysis = analyze_table(_table_with(files), "demo", "events", scan_at=SCANNED_AT)

    assert analysis.small_files_count == 0
    assert analysis.raw["files_under_8mib"] == 200
    assert _small_file_deductions(analysis) == []


def test_compactable_small_files_drive_the_score_and_the_count() -> None:
    files = []
    for day in range(70):
        partition = (f"2026-02-{day:03d}",)
        files.append(FakeDataFile(1 * MB, 1, partition))
        files.append(FakeDataFile(2 * MB, 1, partition))
    analysis = analyze_table(_table_with(files), "demo", "events", scan_at=SCANNED_AT)

    assert analysis.small_files_count == 140
    (deduction,) = _small_file_deductions(analysis)
    assert "compactable small files" in deduction["reason"]


def test_metadata_ratio_is_worst_case_without_live_data() -> None:
    assert metadata_to_data_ratio(4096, 0) == 1.0
    assert metadata_to_data_ratio(0, 0) == 0.0
    assert metadata_to_data_ratio(50, 1000) == 0.05


def test_all_metadata_table_takes_the_metadata_overhead_deduction() -> None:
    stats = enrich_stats_signals(
        {"total_files": 0, "total_size_gb": 0.0}, metadata_bytes=10 * MB, data_bytes=0
    )
    assert stats["metadata_to_data_ratio"] == 1.0
    _, _, deductions = compute_health_score(stats, 0, now=SCANNED_AT)
    assert any(d["label"] == "Metadata ratio overhead" for d in deductions)
