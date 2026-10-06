from __future__ import annotations

from cryo.scheduler.cron import normalize_cron_expression, validate_cron_expression


def validate_cron_field(value: str | None, *, required: bool) -> str | None:
    if value is None:
        return None

    if not value.strip():
        if required:
            raise ValueError("Cron expression is required")
        return None

    validate_cron_expression(value, raise_error=True)
    return normalize_cron_expression(value)
