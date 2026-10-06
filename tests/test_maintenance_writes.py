"""Read-only installs must not enqueue Iceberg writes."""

from cryo.config import Settings
from cryo.maintenance.writes import MaintenanceWritesDisabled
from cryo.queue.redis_queue import InMemoryMaintenanceQueue


def test_enqueue_refuses_when_writes_are_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "cryo.config.get_settings",
        lambda: Settings(maintenance_writes_enabled=False),
    )
    queue = InMemoryMaintenanceQueue()
    try:
        queue.enqueue("job-1", "dest-1")
    except MaintenanceWritesDisabled:
        return
    raise AssertionError("enqueue should refuse when writes are disabled")


def test_enqueue_allows_writes_by_default() -> None:
    InMemoryMaintenanceQueue().enqueue("job-1", "dest-1")
