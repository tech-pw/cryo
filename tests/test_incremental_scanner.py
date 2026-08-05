from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from cryo.scanner.incremental import CommitDelta, apply_commit_delta


class _FakeSession:
    def __init__(self, stats: object | None) -> None:
        self._stats = stats

    def get(self, model: object, key: object) -> object | None:
        del model, key
        return self._stats


def _stats(table_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        table_id=table_id,
        snapshot_watermark=10,
        scan_at=datetime(2026, 7, 1, tzinfo=UTC),
        total_files=100,
        total_size_gb=10.0,
        total_delete_files=4,
        equality_delete_files=1,
        position_delete_files=3,
        delete_file_pct=4.0,
        query_count_30d=120,
        small_files_count=300,
        raw={},
        detection_tier=None,
    )


def test_apply_commit_delta_updates_stats_and_watermark() -> None:
    table_id = uuid.uuid4()
    stats = _stats(table_id)
    session = _FakeSession(stats)
    committed_at = datetime(2026, 7, 8, tzinfo=UTC)

    applied = apply_commit_delta(
        session,
        CommitDelta(
            table_id=table_id,
            snapshot_id=11,
            parent_snapshot_id=10,
            files_added=8,
            files_removed=3,
            bytes_added=2 * 1024**3,
            bytes_removed=512 * 1024**2,
            delete_files_added=2,
            equality_delete_files_added=1,
            position_delete_files_added=1,
            committed_at=committed_at,
        ),
    )

    assert applied is True
    assert stats.snapshot_watermark == 11
    assert stats.last_snapshot_committed_at == committed_at
    assert stats.total_files == 105
    assert stats.total_delete_files == 6
    assert stats.equality_delete_files == 2
    assert stats.position_delete_files == 4
    assert stats.detection_tier is not None
    assert "smart_maintenance" in stats.raw
    assert stats.raw["last_snapshot_committed_at"] == committed_at.isoformat()


def test_apply_commit_delta_is_idempotent_on_old_snapshot() -> None:
    table_id = uuid.uuid4()
    stats = _stats(table_id)
    session = _FakeSession(stats)

    applied = apply_commit_delta(
        session,
        CommitDelta(
            table_id=table_id,
            snapshot_id=10,
            parent_snapshot_id=9,
            files_added=1,
            files_removed=1,
            bytes_added=0,
            bytes_removed=0,
            delete_files_added=0,
        ),
    )

    assert applied is False
    assert stats.snapshot_watermark == 10
