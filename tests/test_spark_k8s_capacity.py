"""Move 6, Spark side -- live-capacity accounting from real Spark-on-K8s dispatch Jobs.

Replaces the ``used=0`` hardcode in LiveCapacityProvider's Spark-K8s branch
(previously ``_static_capacity``) with a real count of active dispatch Jobs, so a
destination's slot_cap is enforced against what Kubernetes actually reports running.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from cryo.engines.spark.job_state import SparkJobStateReader
from cryo.governor.capacity import LiveCapacityProvider
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import DestinationConfig


def _make_job(
    *,
    name: str,
    active: int,
    destination: str | None = "default",
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace="cryo",
            labels={
                "app.kubernetes.io/component": "spark-fallback",
                "cryo/destination": destination,
            },
        ),
        status=SimpleNamespace(active=active, succeeded=0, failed=0),
    )


def _make_list_response(jobs: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(items=jobs)


def test_reader_returns_none_when_k8s_unavailable() -> None:
    reader = SparkJobStateReader(batch_api=None)
    assert reader.is_available() is False
    assert reader.list_active_spark_jobs() is None


def test_reader_lists_active_jobs() -> None:
    jobs = [_make_job(name="cryo-spark-a", active=1), _make_job(name="cryo-spark-b", active=1)]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = SparkJobStateReader(batch_api=batch_api, namespace="cryo")

    assert reader.is_available() is True
    active = reader.list_active_spark_jobs()
    assert active is not None
    assert len(active) == 2
    batch_api.list_namespaced_job.assert_called_once_with(
        namespace="cryo", label_selector="app.kubernetes.io/component=spark-fallback"
    )


def test_reader_ignores_finished_jobs() -> None:
    jobs = [
        _make_job(name="cryo-running", active=1),
        SimpleNamespace(
            metadata=SimpleNamespace(
                name="cryo-succeeded", namespace="cryo", labels={"cryo/destination": "default"}
            ),
            status=SimpleNamespace(active=0, succeeded=1, failed=0),
        ),
    ]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = SparkJobStateReader(batch_api=batch_api)

    active = reader.list_active_spark_jobs()
    assert active is not None
    assert [job.name for job in active] == ["cryo-running"]


def test_reader_counts_pending_jobs_as_capacity() -> None:
    pending = _make_job(name="cryo-pending", active=0)
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response([pending])
    reader = SparkJobStateReader(batch_api=batch_api)

    active = reader.list_active_spark_jobs()
    assert active is not None
    assert [job.name for job in active] == ["cryo-pending"]


def test_reader_filters_by_destination() -> None:
    jobs = [
        _make_job(name="cryo-x", active=1, destination="dest-x"),
        _make_job(name="cryo-y", active=1, destination="dest-y"),
    ]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = SparkJobStateReader(batch_api=batch_api)

    filtered = reader.list_active_spark_jobs(destination_id="dest-x")
    assert filtered is not None
    assert [job.name for job in filtered] == ["cryo-x"]


def test_live_capacity_provider_spark_k8s_uses_live_job_count() -> None:
    jobs = [
        _make_job(name="cryo-spark-1", active=1, destination="dest"),
        _make_job(name="cryo-spark-2", active=1, destination="dest"),
    ]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = SparkJobStateReader(batch_api=batch_api)

    provider = LiveCapacityProvider(InMemoryMaintenanceQueue(), spark_job_state_reader=reader)
    live = provider.probe(
        DestinationConfig(
            id="dest",
            engine="spark_emr",
            config={"slot_cap": 6, "submit_mode": "k8s"},
        )
    )
    assert live["default"].used == 2
    assert live["default"].available == 4


def test_live_capacity_provider_spark_k8s_ignores_other_destinations_jobs() -> None:
    jobs = [
        _make_job(name="cryo-spark-1", active=1, destination="dest-a"),
        _make_job(name="cryo-spark-2", active=1, destination="dest-b"),
        _make_job(name="cryo-spark-3", active=1, destination="dest-b"),
    ]
    batch_api = MagicMock()
    batch_api.list_namespaced_job.return_value = _make_list_response(jobs)
    reader = SparkJobStateReader(batch_api=batch_api)

    provider = LiveCapacityProvider(InMemoryMaintenanceQueue(), spark_job_state_reader=reader)
    live = provider.probe(
        DestinationConfig(
            id="dest-a",
            engine="spark_emr",
            config={"slot_cap": 6, "submit_mode": "k8s"},
        )
    )
    assert live["default"].used == 1
    assert live["default"].available == 5


def test_live_capacity_provider_spark_k8s_falls_back_to_zero_when_k8s_unavailable() -> None:
    """No kubernetes client / not in-cluster (local dev, tests without a k8s fixture) --
    preserve the pre-fix behavior instead of refusing all dispatches."""
    provider = LiveCapacityProvider(
        InMemoryMaintenanceQueue(), spark_job_state_reader=SparkJobStateReader(batch_api=None)
    )
    live = provider.probe(
        DestinationConfig(
            id="dest",
            engine="spark_emr",
            config={"slot_cap": 3, "submit_mode": "k8s"},
        )
    )
    assert live["default"].used == 0
    assert live["default"].available == 3


def test_live_capacity_provider_spark_k8s_fails_closed_on_read_error() -> None:
    reader = MagicMock()
    reader.is_available.return_value = True
    reader.list_active_spark_jobs.return_value = None

    provider = LiveCapacityProvider(InMemoryMaintenanceQueue(), spark_job_state_reader=reader)
    live = provider.probe(
        DestinationConfig(
            id="dest",
            engine="spark_emr",
            config={"slot_cap": 4, "submit_mode": "k8s"},
        )
    )
    assert live["default"].used == 4
    assert live["default"].available == 0


def test_spark_emr_submit_mode_still_probes_emr_not_k8s() -> None:
    """Only submit_mode="k8s" should route through the live Job-count probe -- real EMR
    Serverless destinations must keep going through _probe_emr."""
    provider = LiveCapacityProvider(InMemoryMaintenanceQueue())
    live = provider.probe(
        DestinationConfig(
            id="dest",
            engine="spark_emr",
            config={"slot_cap": 4, "application_id": "app-123"},
        )
    )
    # _probe_emr swallows failures (no real AWS creds in tests) and reports used=0.
    assert live["emr"].used == 0
