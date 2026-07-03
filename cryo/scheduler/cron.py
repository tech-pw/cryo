from __future__ import annotations

from datetime import UTC, datetime, timedelta


def cron_is_due(
    expression: str | None,
    *,
    now: datetime | None = None,
    last_run_at: datetime | None = None,
) -> bool:
    """Return True if a cron expression is due to fire.

    Supports standard 5-field cron: minute hour dom month dow.
    Uses a simple matcher without external dependencies.
    """
    if not expression:
        return False

    current = _ensure_aware(now or datetime.now(UTC))
    fields = expression.strip().split()
    if len(fields) != 5:
        return False

    minute, hour, dom, month, dow = fields
    if not _field_matches(minute, current.minute, 0, 59):
        return False
    if not _field_matches(hour, current.hour, 0, 23):
        return False
    if not _field_matches(dom, current.day, 1, 31):
        return False
    if not _field_matches(month, current.month, 1, 12):
        return False
    if not _field_matches(dow, current.weekday(), 0, 6):
        return False

    if last_run_at is not None:
        last = _ensure_aware(last_run_at)
        if current.replace(second=0, microsecond=0) <= last.replace(second=0, microsecond=0):
            return False
    return True


def next_cron_tick(expression: str, *, now: datetime | None = None) -> datetime:
    """Return the next datetime when the cron expression would fire."""
    current = _ensure_aware(now or datetime.now(UTC))
    probe = current.replace(second=0, microsecond=0)
    for _ in range(60 * 24 * 366):
        if cron_is_due(expression, now=probe, last_run_at=probe - timedelta(minutes=1)):
            return probe
        probe += timedelta(minutes=1)
    return probe


def _field_matches(field: str, value: int, minimum: int, maximum: int) -> bool:
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return value % step == 0
    if "," in field:
        return any(_field_matches(part, value, minimum, maximum) for part in field.split(","))
    if "-" in field and not field.startswith("-"):
        start, end = field.split("-", 1)
        return int(start) <= value <= int(end)
    return int(field) == value


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
