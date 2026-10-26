"""The install's display and scheduling time zone (CRYO_DISPLAY_TIMEZONE, #183)."""

from __future__ import annotations

import logging
from datetime import UTC, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


@lru_cache(maxsize=16)
def zone_for(name: str | None) -> tzinfo:
    """ZoneInfo for an IANA name; UTC (with a warning) when it is empty or unknown."""
    if not name or name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown time zone %r; using UTC", name)
        return UTC


def display_zone() -> tzinfo:
    from cryo.config import get_settings

    return zone_for(get_settings().display_timezone)


def display_zone_name() -> str:
    zone = display_zone()
    return "UTC" if zone is UTC else str(zone)
