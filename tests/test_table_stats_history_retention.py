from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from cryo.db.models import Source, Table, TableStatsHistory
from cryo.db.repositories.table_stats_history_retention import prune_table_stats_history


def _table_with_history(session) -> uuid.UUID:
    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4().hex[:8]}", type="rest", config={})
    table = Table(
        id=uuid.uuid4(), source_id=source.id, schema_name="s", table_name="t", is_active=True
    )
    session.add_all([source, table])
    session.flush()
    return table.id


def _snapshot(table_id: uuid.UUID, scan_at: datetime) -> TableStatsHistory:
    return TableStatsHistory(table_id=table_id, scan_at=scan_at, total_files=1, grade="A")


def test_prune_table_stats_history_keeps_rows_inside_retention(session) -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    table_id = _table_with_history(session)
    session.add_all(
        [
            _snapshot(table_id, now - timedelta(days=181)),
            _snapshot(table_id, now - timedelta(days=180)),
            _snapshot(table_id, now - timedelta(days=1)),
        ]
    )
    session.commit()

    result = prune_table_stats_history(session, 180, now=now, batch_size=10, max_rows=10)

    assert result.deleted == 1
    assert result.remaining == 0
    assert session.scalar(select(func.count(TableStatsHistory.id))) == 2


def test_prune_table_stats_history_bounds_work_and_reports_backlog(session) -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    table_id = _table_with_history(session)
    session.add_all(
        [
            _snapshot(table_id, now - timedelta(days=200, seconds=index))
            for index in range(5)
        ]
    )
    session.commit()

    result = prune_table_stats_history(session, 180, now=now, batch_size=2, max_rows=3)

    assert result.deleted == 3
    assert result.remaining == 2
    assert session.scalar(select(func.count(TableStatsHistory.id))) == 2


@pytest.mark.parametrize(
    ("retention_days", "batch_size", "max_rows"),
    [(0, 1, 1), (1, 0, 1), (1, 1, 0)],
)
def test_prune_table_stats_history_rejects_invalid_limits(
    session,
    retention_days: int,
    batch_size: int,
    max_rows: int,
) -> None:
    with pytest.raises(ValueError):
        prune_table_stats_history(
            session,
            retention_days,
            batch_size=batch_size,
            max_rows=max_rows,
        )
