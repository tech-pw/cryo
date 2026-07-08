"""Move 6 — live-capacity accounting from real Kubernetes Job state.

Replaces the ``used=0`` hardcode in ``LiveCapacityProvider._probe_native`` with
counts and resource totals derived from real K8s Jobs (CPU/memory requests).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from cryo.domain.models import CapacitySnapshot
from cryo.engines.native.job_state import (
    NativeJobStateReader,
    parse_cpu_to_milli,
    parse_memory_to_mib,
    summarize_native_jobs,
)
from cryo.governor.capacity import (
    LiveCapacityProvider,
    probe_native_capacity,
    probe_native_capacity_by_replica,
)
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import DestinationConfig


def _make_job(
    *,
    name: str,
    active: int,
    destination: str = "default",
    cpu: str = "500m",
    memory: str = "1Gi",
    destination_label: str | None = None,
    table_ref: str = "db.tbl",
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace="default",
            labels={
                "app.kubernetes.io/component": "native-engine",
                "cryo/destination": (
                    destination_label if destination_label is not None else destination
                ),
                "cryo/table_ref": table_ref,
            },
            annotations={"cryo/cpu": cpu, "cryo/memory": memory},
        ),
        status=SimpleNamespace(active=active, succeeded=0, failed=0),
        spec=SimpleNamespace(template=SimpleNamespace(spec=None)),
    )


def _make_list_response(jobs: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(items=jobs)


def test_parse_cpu_to_milli_handles_suffixes() -> None:
    assert parse_cpu_to_milli("250m") == 250
    assert parse_cpu_to_milli("1") == 1000
    assert parse_cpu_to_milli("0.5") == 500
    assert parse_cpu_to_milli("  2000m  ") == 2000
    assert parse_cpu_to_milli(None) == 0
    assert parse_cpu_to_milli("") == 0


def test_parse_memory_to_mib_handles_suffixes() -> None:
    assert parse_memory_to_mib("512Mi") == 512
    assert parse_memory_to_mib("2Gi") == 2048
    assert parse_memory_to_mib("1048576Ki") == 1024
    assert parse_memory_to_mib(None) == 0
    assert parse_memory_to_mib("") == 0


def test_summarize_native_jobs_returns_zero_for_empty() -> None:
    totals = summarize_native_jobs(())
    assert totals.job_count == 0
    assert totals.cpu_used_milli == 0
    assert totals.mem_used_mib == 0


def test_reader_returns_none_when_k8s_unavailable() -> None:
    reader = NativeJobStateReader(batch_api=None)  # no in-cluster config either
    assert reader.is_available() is False
    assert reader.list_active_native_jobs() is None


def test_reader_lists_jobs_and_parses_resources_from_annotations() -> None:
    jobs = [
        _make_job(name="cryo-a", active=1, cpu="250m", memory="512Mi"),
        _make_job(name="cryo-b", active=1, cpu="1500m", memory="4Gi"),
    ]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = NativeJobStateReader(batch_api=batch_api)

    assert reader.is_available() is True
    active = reader.list_active_native_jobs()
    assert active is not None
    assert len(active) == 2

    totals = summarize_native_jobs(active)
    assert totals.job_count == 2
    assert totals.cpu_used_milli == 1750
    assert totals.mem_used_mib == 4608


def test_reader_filters_by_destination() -> None:
    jobs = [
        _make_job(name="cryo-a", active=1, destination="dest-x"),
        _make_job(name="cryo-b", active=1, destination="dest-y"),
    ]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = NativeJobStateReader(batch_api=batch_api)

    filtered = reader.list_active_native_jobs(destination_id="dest-x")
    assert filtered is not None
    assert [job.name for job in filtered] == ["cryo-a"]


def test_reader_ignores_finished_jobs() -> None:
    jobs = [
        _make_job(name="cryo-running", active=1),
        SimpleNamespace(
            metadata=SimpleNamespace(
                name="cryo-succeeded",
                namespace="default",
                labels={"cryo/destination": "default"},
                annotations={"cryo/cpu": "250m", "cryo/memory": "512Mi"},
            ),
            status=SimpleNamespace(active=0, succeeded=1, failed=0),
            spec=SimpleNamespace(template=SimpleNamespace(spec=None)),
        ),
    ]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = NativeJobStateReader(batch_api=batch_api)

    active = reader.list_active_native_jobs()
    assert active is not None
    assert [job.name for job in active] == ["cryo-running"]


def test_reader_falls_back_to_pod_spec_when_annotations_missing() -> None:
    resources = SimpleNamespace(
        requests={"cpu": "2000m", "memory": "8Gi"},
    )
    container = SimpleNamespace(resources=resources)
    pod_spec = SimpleNamespace(containers=[container])
    template = SimpleNamespace(spec=pod_spec)
    job = SimpleNamespace(
        metadata=SimpleNamespace(
            name="cryo-c",
            namespace="default",
            labels={"cryo/destination": "default"},
            annotations={},
        ),
        status=SimpleNamespace(active=1, succeeded=0, failed=0),
        spec=SimpleNamespace(template=template),
    )
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response([job])
    reader = NativeJobStateReader(batch_api=batch_api)

    active = reader.list_active_native_jobs()
    assert active is not None
    assert active[0].cpu_milli == 2000
    assert active[0].mem_mib == 8192


def test_probe_native_capacity_uses_k8s_state_when_available() -> None:
    queue = InMemoryMaintenanceQueue()
    jobs = [_make_job(name="cryo-j", active=1, cpu="1000m", memory="2Gi")]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = NativeJobStateReader(batch_api=batch_api)

    snapshot = probe_native_capacity(
        {"slot_cap": 5},
        destination_id="default",
        queue=queue,
        job_state_reader=reader,
    )
    assert snapshot.used == 1
    assert snapshot.queued == 1
    assert snapshot.available == 4
    assert snapshot.cpu_used_milli == 1000
    assert snapshot.mem_used_mib == 2048


def test_probe_native_capacity_falls_back_to_queue() -> None:
    queue = InMemoryMaintenanceQueue()
    queue.admit("job-1", "dest", "default")
    queue.admit("job-2", "dest", "default")

    snapshot = probe_native_capacity(
        {"slot_cap": 5},
        destination_id="dest",
        queue=queue,
        job_state_reader=None,
    )
    assert snapshot.used == 2
    assert snapshot.available == 3


def test_probe_native_capacity_takes_max_of_queue_and_k8s() -> None:
    queue = InMemoryMaintenanceQueue()
    queue.admit("job-1", "dest", "default")
    jobs = [
        _make_job(name="cryo-a", active=1, destination="dest", cpu="250m", memory="512Mi"),
        _make_job(name="cryo-b", active=1, destination="dest", cpu="250m", memory="512Mi"),
        _make_job(name="cryo-c", active=1, destination="dest", cpu="250m", memory="512Mi"),
    ]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = NativeJobStateReader(batch_api=batch_api)

    snapshot = probe_native_capacity(
        {"slot_cap": 5},
        destination_id="dest",
        queue=queue,
        job_state_reader=reader,
    )
    # 3 K8s Jobs > 1 queue in-flight; conservative max means admission sees 3.
    assert snapshot.used == 3
    assert snapshot.available == 2
    assert snapshot.cpu_used_milli == 750
    assert snapshot.mem_used_mib == 1536


def test_probe_native_capacity_by_replica_with_k8s() -> None:
    queue = InMemoryMaintenanceQueue()
    jobs = [
        _make_job(name="cryo-a", active=1, destination="dest", cpu="500m", memory="1Gi"),
        _make_job(name="cryo-b", active=1, destination="dest", cpu="1500m", memory="2Gi"),
    ]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = NativeJobStateReader(batch_api=batch_api)

    by_replica = probe_native_capacity_by_replica(
        {"slot_cap": 5, "replicas": ["r1", "r2"]},
        destination_id="dest",
        queue=queue,
        job_state_reader=reader,
    )
    # K8s mode collapses all Jobs into a single bucket keyed by destination_id;
    # per-replica round-robin is a subprocess-only concept.
    assert by_replica["dest"].used == 2
    assert by_replica["dest"].available == 3
    assert by_replica["dest"].cpu_used_milli == 2000  # 500m + 1500m
    assert by_replica["dest"].mem_used_mib == 3072  # 1024 + 2048
    assert by_replica["r1"].used == 0
    assert by_replica["r2"].used == 0


def test_live_capacity_provider_native_uses_k8s_reader() -> None:
    queue = InMemoryMaintenanceQueue()
    jobs = [
        _make_job(name="cryo-x", active=1, destination="dest", cpu="500m", memory="1Gi"),
        _make_job(name="cryo-y", active=1, destination="dest", cpu="500m", memory="1Gi"),
    ]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = NativeJobStateReader(batch_api=batch_api)

    provider = LiveCapacityProvider(queue, job_state_reader=reader)
    live = provider.probe(
        DestinationConfig(id="dest", engine="native", config={"slot_cap": 10}),
    )
    # K8s mode collapses per-destination into one bucket keyed by destination_id.
    assert live["dest"].used == 2
    assert live["dest"].available == 8
    assert live["dest"].cpu_used_milli == 1000
    assert live["dest"].mem_used_mib == 2048


def test_capacity_snapshot_backward_compatible_defaults() -> None:
    # Tests/callers that don't touch K8s fields must keep working unchanged.
    snapshot = CapacitySnapshot(capacity=4, used=1, queued=1)
    assert snapshot.cpu_used_milli == 0
    assert snapshot.mem_used_mib == 0
    assert snapshot.available == 3
