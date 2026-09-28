"""Gate that keeps a read-only install from writing Iceberg."""

from __future__ import annotations


class MaintenanceWritesDisabled(RuntimeError):
    """Raised when a write path runs while maintenance writes are turned off."""


def maintenance_writes_enabled() -> bool:
    from cryo.config import get_settings

    return bool(get_settings().maintenance_writes_enabled)


def ensure_maintenance_writes_enabled() -> None:
    if not maintenance_writes_enabled():
        raise MaintenanceWritesDisabled(
            "maintenance writes are disabled; this install can scan and report only"
        )
