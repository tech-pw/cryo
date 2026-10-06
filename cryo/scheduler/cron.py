from __future__ import annotations

from datetime import UTC, datetime, timedelta
from datetime import tzinfo as TzInfo

MAX_CRON_EXPRESSION_LENGTH = 100
_ALLOWED_CRON_CHARS = frozenset("0123456789*/-, ")


# How far back cron_is_due will scan looking for a missed fire-time before giving up and
# reporting not-due. See cron_is_due's own docstring for the incident this bounds against --
# 24h covers every real schedule_cron in the fleet (all once-daily) with room to spare, without
# risking an unbounded scan if a caller passes a wildly stale last_run_at.
_MISSED_FIRE_LOOKBACK = timedelta(hours=24)


def _fields_match_minute(fields: list[str], moment: datetime) -> bool:
    minute, hour, dom, month, dow = fields
    if not (
        _field_matches(minute, moment.minute, 0, 59)
        and _field_matches(hour, moment.hour, 0, 23)
        and _field_matches(month, moment.month, 1, 12)
    ):
        return False
    # Standard cron ORs day-of-month and day-of-week when both are restricted.
    dom_match = _field_matches(dom, moment.day, 1, 31)
    # 0 = Sunday. datetime.weekday() is Monday = 0.
    dow_match = _field_matches(dow, (moment.weekday() + 1) % 7, 0, 6)
    if dom == "*" and dow == "*":
        return True
    if dom == "*":
        return dow_match
    if dow == "*":
        return dom_match
    return dom_match or dow_match


def cron_is_due(
    expression: str | None,
    *,
    now: datetime | None = None,
    last_run_at: datetime | None = None,
    tz: TzInfo | str | None = None,
) -> bool:
    """Return True if a cron expression has a fire-time due, scanning back up to
    ``_MISSED_FIRE_LOOKBACK`` for one that hasn't been run yet.

    Supports standard 5-field cron: minute hour dom month dow.
    Uses a simple matcher without external dependencies.

    ``tz`` converts ``now`` before matching clock fields so expressions like
    ``0 10 * * *`` can mean 10:00 in ``Asia/Kolkata`` rather than 10:00 UTC.

    Scans backward minute-by-minute from ``now`` (previously: checked only the exact current
    minute) so a tick that runs even slightly late doesn't silently skip that day's fire
    entirely. Found live, 2026-08-28..31: the scheduler daemon's tick() loop calls this once per
    minute, but a single tick's own duration had grown past 60s (each of several sequential
    per-table metastore-connect attempts now individually bounded at 30s, see
    layout_analyzer.py's own timeout-fix comment for why that bound exists at all -- the
    underlying metastore connectivity issue itself was, and still is, real and ongoing). The old
    exact-minute match meant any tick spanning past its cron target minute caused that preset to
    be silently skipped for the entire day, with no retry until the next day's identical window
    -- confirmed live: zero new MaintenanceRun rows created for 3+ days straight across every
    daily preset, despite the scheduler pod itself staying healthy the whole time. The bounded
    backward scan means a late tick still catches yesterday-or-today's missed fire on its very
    next invocation, without needing to hit the literal instant.
    """
    if not expression:
        return False

    current = _ensure_aware(now or datetime.now(UTC))
    if tz is not None:
        current = current.astimezone(_coerce_tz(tz))
    if not validate_cron_expression(expression):
        return False

    fields = normalize_cron_expression(expression).split()
    last = _ensure_aware(last_run_at) if last_run_at is not None else None

    probe = current.replace(second=0, microsecond=0)
    earliest = probe - _MISSED_FIRE_LOOKBACK
    while probe >= earliest:
        if _fields_match_minute(fields, probe):
            # A restricted weekday must not catch up on the next calendar day.
            if fields[4] != "*" and probe.date() != current.date():
                probe -= timedelta(minutes=1)
                continue
            return last is None or probe > last.replace(second=0, microsecond=0)
        probe -= timedelta(minutes=1)
    return False


def next_cron_tick(
    expression: str,
    *,
    now: datetime | None = None,
    tz: TzInfo | str | None = None,
) -> datetime:
    """Return the next datetime when the cron expression would fire.

    Uses the same exact-minute field matcher cron_is_due's backward scan is built on
    (_fields_match_minute), not cron_is_due itself -- cron_is_due now looks *backward* for a
    due-but-unrun fire time (see its own docstring), which is the wrong direction for "what's
    the next one", and would make this walk terminate on its very first iteration whenever the
    cron already fired earlier today.
    """
    validate_cron_expression(expression, raise_error=True)
    current = _ensure_aware(now or datetime.now(UTC))
    if tz is not None:
        current = current.astimezone(_coerce_tz(tz))
    fields = normalize_cron_expression(expression).split()
    probe = current.replace(second=0, microsecond=0)
    for _ in range(60 * 24 * 366):
        if _fields_match_minute(fields, probe):
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
    if "," in field:
        return any(_field_matches(part, value, minimum, maximum) for part in field.split(","))
    if field.startswith("*/"):
        step = int(field[2:])
        return step > 0 and (value - minimum) % step == 0
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
