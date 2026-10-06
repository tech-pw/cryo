"""Native subprocess capacity probing (Phase E / rust finding #9)."""

from __future__ import annotations

from cryo.domain.models import CapacitySnapshot
from cryo.engines.native.engine import NativeEngine
from cryo.governor.admission import AdmissionConfig, compute_admission_budget
from cryo.governor.capacity import (
    LiveCapacityProvider,
    probe_native_capacity,
    probe_native_capacity_by_replica,
)
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import DestinationConfig


def test_probe_native_capacity_without_queue_reports_zero_used() -> None:
    snapshot = probe_native_capacity({"slot_cap": 4})
    assert snapshot == CapacitySnapshot(capacity=4, used=0, queued=0)
    assert snapshot.available == 4


def test_probe_native_capacity_counts_queue_in_flight() -> None:
    queue = InMemoryMaintenanceQueue()
    queue.admit("job-1", "native-dest", "default")
    queue.admit("job-2", "native-dest", "default")

    snapshot = probe_native_capacity(
        {"slot_cap": 5},
        destination_id="native-dest",
        queue=queue,
    )
    assert snapshot.used == 2
    assert snapshot.available == 3


def test_probe_native_capacity_by_replica() -> None:
    queue = InMemoryMaintenanceQueue()
    queue.admit("job-a", "dest", "r1")
    queue.admit("job-b", "dest", "r2")
    queue.admit("job-c", "dest", "r2")

    by_replica = probe_native_capacity_by_replica(
        {"slot_cap": 3, "replicas": ["r1", "r2"]},
        destination_id="dest",
        queue=queue,
    )
    assert by_replica["r1"].used == 1
    assert by_replica["r2"].used == 2
    assert by_replica["r1"].available == 2


def test_live_capacity_provider_native_uses_queue() -> None:
    queue = InMemoryMaintenanceQueue()
    queue.admit("running", "dest", "default")
    provider = LiveCapacityProvider(queue)
    live = provider.probe(
        DestinationConfig(id="dest", engine="native", config={"slot_cap": 6}),
    )
    assert live["default"].used == 1
    assert live["default"].available == 5


def test_governor_admission_uses_max_of_live_and_local_in_flight() -> None:
    queue = InMemoryMaintenanceQueue()
    queue.admit("job", "dest", "default")
    live = probe_native_capacity_by_replica(
        {"slot_cap": 4},
        destination_id="dest",
        queue=queue,
    )
    budget = compute_admission_budget(
        live,
        queue.local_in_flight_by_replica("dest"),
        AdmissionConfig(per_replica_cap=4),
    )
    assert budget.total_available == 3


def test_native_engine_probe_capacity_honors_destination_id_config() -> None:
    queue = InMemoryMaintenanceQueue()
    queue.admit("job", "api-dest", "default")
    provider = LiveCapacityProvider(queue)
    live = provider.probe(
        DestinationConfig(id="api-dest", engine="native", config={"slot_cap": 8}),
    )
    engine = NativeEngine({"slot_cap": 8, "destination_id": "api-dest"})
    # Engine probe without Redis falls back to zero in-flight; governor path uses queue directly.
    assert engine.probe_capacity().capacity == 8
    assert live["default"].used == 1
