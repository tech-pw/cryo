"""Archive Ask Cryo conversations past the retention window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AssistantConversation


def purge_expired_conversations(
    session: Session, *, retention_days: int, now: datetime | None = None
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    rows = session.scalars(
        select(AssistantConversation).where(
            AssistantConversation.deleted_at.is_(None),
            AssistantConversation.updated_at < cutoff,
        )
    ).all()
    stamped = now or datetime.now(UTC)
    for row in rows:
        row.deleted_at = stamped
        row.status = "archived"
    session.flush()
    return len(rows)
