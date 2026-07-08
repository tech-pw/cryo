from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from cryo.db.models import DeletionTracking
from cryo.db.repositories.deletion_tracking import (
    DeletionTrackingRepository,
    PendingDeletionSummary,
)


def _repo_with_pending(
    rows: list[DeletionTracking],
) -> tuple[MagicMock, DeletionTrackingRepository]:
    session = MagicMock()
    scalar_result = MagicMock()
    scalar_result.all.return_value = rows
    session.scalars.return_value = scalar_result
    return session, DeletionTrackingRepository(session)


def test_record_persists_row() -> None:
    session = MagicMock()
    repo = DeletionTrackingRepository(session)
    repo.record(
        table_id=uuid4(),
        subject_id="user:42",
        soft_deleted_at=datetime.now(UTC),
        pending_rows=10,
        blocked_until=datetime.now(UTC) + timedelta(days=7),
        reason="GDPR right-to-erasure",
    )
    assert session.add.called
    assert session.commit.called


def test_record_many_handles_empty() -> None:
    session = MagicMock()
    repo = DeletionTrackingRepository(session)
    assert repo.record_many([]) == 0
    session.add_all.assert_not_called()


def test_mark_resolved_returns_count_and_stamps_resolved_at() -> None:
    table_id = uuid4()
    now = datetime.now(UTC)
    rows = [
        DeletionTracking(
            table_id=table_id,
            subject_id="user:1",
            soft_deleted_at=now,
            pending_rows=5,
            blocked_until=now - timedelta(days=1),
            reason="x",
        ),
        DeletionTracking(
            table_id=table_id,
            subject_id="user:2",
            soft_deleted_at=now,
            pending_rows=3,
            blocked_until=now + timedelta(days=10),
            reason="x",
        ),
    ]
    session, repo = _repo_with_pending(rows)
    resolved = repo.mark_resolved(table_id, up_to=now)
    assert resolved == 2
    for row in rows:
        assert row.resolved_at is not None


def test_pending_for_table_returns_only_unresolved() -> None:
    table_id = uuid4()
    now = datetime.now(UTC)
    rows = [
        DeletionTracking(
            table_id=table_id,
            subject_id="user:a",
            soft_deleted_at=now,
            pending_rows=1,
            blocked_until=now,
            reason="x",
        ),
    ]
    _, repo = _repo_with_pending(rows)
    assert repo.pending_for_table(table_id) == rows


def test_fleet_summary_groups_by_table() -> None:
    session = MagicMock()
    table_a, table_b = uuid4(), uuid4()
    now = datetime.now(UTC)
    expected = [
        MagicMock(
            table_id=table_a,
            rows=100,
            earliest=now - timedelta(days=2),
            blocked_until=now + timedelta(days=5),
            reason="GDPR right-to-erasure",
        ),
        MagicMock(
            table_id=table_b,
            rows=50,
            earliest=now - timedelta(days=1),
            blocked_until=now + timedelta(days=10),
            reason="CCPA opt-out",
        ),
    ]
    session.execute.return_value.all.return_value = expected
    repo = DeletionTrackingRepository(session)
    summaries = repo.fleet_summary()
    assert len(summaries) == 2
    assert isinstance(summaries[0], PendingDeletionSummary)
    assert summaries[0].pending_rows == 100
    assert summaries[1].pending_rows == 50


def test_total_pending_rows_sums_unresolved() -> None:
    session = MagicMock()
    session.execute.return_value.scalar.return_value = 4242
    repo = DeletionTrackingRepository(session)
    assert repo.total_pending_rows() == 4242


def test_total_pending_rows_handles_zero() -> None:
    session = MagicMock()
    session.execute.return_value.scalar.return_value = None
    repo = DeletionTrackingRepository(session)
    assert repo.total_pending_rows() == 0
