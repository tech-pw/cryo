from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from cryo.db.models import AuditLog
from cryo.db.repositories.audit_retention import prune_audit_logs


def _audit(ts: datetime, action: str) -> AuditLog:
    return AuditLog(ts=ts, action=action, status="success")


def test_prune_audit_logs_keeps_rows_inside_retention(session) -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    session.add_all(
        [
            _audit(now - timedelta(days=366), "expired"),
            _audit(now - timedelta(days=365), "boundary"),
            _audit(now - timedelta(days=1), "recent"),
        ]
    )
    session.commit()

    result = prune_audit_logs(session, 365, now=now, batch_size=10, max_rows=10)

    assert result.deleted == 1
    assert result.remaining == 0
    assert list(session.scalars(select(AuditLog.action).order_by(AuditLog.action))) == [
        "boundary",
        "recent",
    ]


def test_prune_audit_logs_bounds_work_and_reports_backlog(session) -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    session.add_all(
        [_audit(now - timedelta(days=400, seconds=index), f"old-{index}") for index in range(5)]
    )
    session.commit()

    result = prune_audit_logs(session, 365, now=now, batch_size=2, max_rows=3)

    assert result.deleted == 3
    assert result.remaining == 2
    assert session.scalar(select(func.count(AuditLog.id))) == 2


@pytest.mark.parametrize(
    ("retention_days", "batch_size", "max_rows"),
    [(0, 1, 1), (1, 0, 1), (1, 1, 0)],
)
def test_prune_audit_logs_rejects_invalid_limits(
    session,
    retention_days: int,
    batch_size: int,
    max_rows: int,
) -> None:
    with pytest.raises(ValueError):
        prune_audit_logs(
            session,
            retention_days,
            batch_size=batch_size,
            max_rows=max_rows,
        )
