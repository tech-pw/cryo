from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from cryo.db.models import ProtectedSnapshot
from cryo.db.repositories.protected_snapshots import ProtectedSnapshotRepository


def _make_repo_with_rows(snapshot_ids: list[int]) -> tuple[MagicMock, ProtectedSnapshotRepository]:
    session = MagicMock()
    session.scalars.return_value.all.return_value = [
        ProtectedSnapshot(snapshot_id=sid) for sid in snapshot_ids
    ]
    return session, ProtectedSnapshotRepository(session)


def test_list_for_table_returns_rows_in_order() -> None:
    session, repo = _make_repo_with_rows([100, 200, 300])
    rows = repo.list_for_table(uuid4())
    assert [r.snapshot_id for r in rows] == [100, 200, 300]
    session.scalars.assert_called_once()


def test_list_snapshot_ids_returns_set() -> None:
    session = MagicMock()
    session.execute.return_value.all.return_value = [(100,), (200,)]
    ids = ProtectedSnapshotRepository(session).list_snapshot_ids(uuid4())
    assert ids == {100, 200}


def test_protect_creates_new_row_when_missing() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    repo = ProtectedSnapshotRepository(session)
    repo.protect(table_id=uuid4(), snapshot_id=42, label="before-migration")
    assert session.add.called
    assert session.commit.called


def test_protect_returns_existing_row_when_present() -> None:
    session = MagicMock()
    existing = ProtectedSnapshot(snapshot_id=42, label="old")
    session.scalar.return_value = existing
    repo = ProtectedSnapshotRepository(session)
    row = repo.protect(table_id=uuid4(), snapshot_id=42, label=None)
    assert row is existing
    session.add.assert_not_called()


def test_protect_updates_label_when_existing_and_label_provided() -> None:
    session = MagicMock()
    existing = ProtectedSnapshot(snapshot_id=42, label="old")
    session.scalar.return_value = existing
    repo = ProtectedSnapshotRepository(session)
    row = repo.protect(table_id=uuid4(), snapshot_id=42, label="new-label")
    assert row.label == "new-label"
    assert session.commit.called


def test_unprotect_returns_true_when_row_found() -> None:
    session = MagicMock()
    session.scalar.return_value = ProtectedSnapshot(snapshot_id=42)
    repo = ProtectedSnapshotRepository(session)
    assert repo.unprotect(table_id=uuid4(), snapshot_id=42) is True
    assert session.delete.called
    assert session.commit.called


def test_unprotect_returns_false_when_missing() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    repo = ProtectedSnapshotRepository(session)
    assert repo.unprotect(table_id=uuid4(), snapshot_id=42) is False
    session.delete.assert_not_called()


def test_partition_expirable_splits_protected() -> None:
    session = MagicMock()
    session.execute.return_value.all.return_value = [(10,), (20,)]
    repo = ProtectedSnapshotRepository(session)
    expirable, kept = repo.partition_expirable(uuid4(), [10, 20, 30, 40])
    assert expirable == {30, 40}
    assert kept == {10, 20}
