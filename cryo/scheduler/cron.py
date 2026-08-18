from __future__ import annotations

from datetime import UTC, datetime, timedelta
from datetime import tzinfo as TzInfo

MAX_CRON_EXPRESSION_LENGTH = 100
_ALLOWED_CRON_CHARS = frozenset("0123456789*/-, ")


def cron_is_due(
    expression: str | None,
    *,
    now: datetime | None = None,
    last_run_at: datetime | None = None,
    tz: TzInfo | str | None = None,
) -> bool:
    """Return True if a cron expression is due to fire.

    Supports standard 5-field cron: minute hour dom month dow.
    Uses a simple matcher without external dependencies.

    ``tz`` converts ``now`` before matching clock fields so expressions like
    ``0 10 * * *`` can mean 10:00 in ``Asia/Kolkata`` rather than 10:00 UTC.
    """
    if not expression:
        return False

    current = _ensure_aware(now or datetime.now(UTC))
    if tz is not None:
        current = current.astimezone(_coerce_tz(tz))
    if not validate_cron_expression(expression):
        return False

    fields = normalize_cron_expression(expression).split()
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


def next_cron_tick(
    expression: str,
    *,
    now: datetime | None = None,
    tz: TzInfo | str | None = None,
) -> datetime:
    """Return the next datetime when the cron expression would fire."""
    validate_cron_expression(expression, raise_error=True)
    current = _ensure_aware(now or datetime.now(UTC))
    probe = current.replace(second=0, microsecond=0)
    for _ in range(60 * 24 * 366):
        if cron_is_due(expression, now=probe, last_run_at=probe - timedelta(minutes=1), tz=tz):
            return probe
        probe += timedelta(minutes=1)
    return probe


def validate_cron_expression(expression: str | None, *, raise_error: bool = False) -> bool:
    """Validate the simple 5-field cron subset supported by Cryo."""
    if not expression:
        return _cron_invalid("Cron expression is required", raise_error=raise_error)

    if len(expression) > MAX_CRON_EXPRESSION_LENGTH:
        return _cron_invalid("Cron expression is too long", raise_error=raise_error)

    if any(char not in _ALLOWED_CRON_CHARS for char in expression):
        return _cron_invalid(
            "Cron expression contains unsupported characters",
            raise_error=raise_error,
        )

    fields = normalize_cron_expression(expression).split()
    if len(fields) != 5:
        return _cron_invalid("Cron expression must have five fields", raise_error=raise_error)

    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
    try:
        for field, (minimum, maximum) in zip(fields, ranges, strict=True):
            _validate_field(field, minimum, maximum)
    except ValueError as exc:
        return _cron_invalid(str(exc), raise_error=raise_error)
    return True


def normalize_cron_expression(expression: str) -> str:
    """Trim and collapse spaces in a cron expression."""
    return " ".join(expression.strip().split())


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


def _validate_field(field: str, minimum: int, maximum: int) -> None:
    if field == "*":
        return
    if "," in field:
        if "" in field.split(","):
            raise ValueError("Cron lists cannot contain empty values")
        for part in field.split(","):
            _validate_field(part, minimum, maximum)
        return
    if field.startswith("*/"):
        step_text = field[2:]
        if not step_text.isdigit():
            raise ValueError("Cron step values must be integers")
        step = int(step_text)
        if step <= 0:
            raise ValueError("Cron step values must be positive")
        return
    if "-" in field and not field.startswith("-"):
        start, end = field.split("-", 1)
        if not start.isdigit() or not end.isdigit():
            raise ValueError("Cron ranges must use integer values")
        start_int = int(start)
        end_int = int(end)
        if start_int > end_int:
            raise ValueError("Cron ranges must be ascending")
        if start_int < minimum or end_int > maximum:
            raise ValueError("Cron range is outside the supported bounds")
        return

    if not field.isdigit():
        raise ValueError("Cron values must be integers")
    value = int(field)
    if value < minimum or value > maximum:
        raise ValueError("Cron value is outside the supported bounds")


def _cron_invalid(message: str, *, raise_error: bool) -> bool:
    if raise_error:
        raise ValueError(message)
    return False


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _coerce_tz(tz: TzInfo | str) -> TzInfo:
    if isinstance(tz, str):
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz)
    return tz
