"""Pods stuck in a non-recovering waiting state fail the step after a grace period (#485)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kubernetes.client.exceptions import ApiException

from cryo.engines.spark.k8s import JobRunState, SparkK8sClient, SparkK8sConfig

_IMAGE = "registry.example/cryo-spark@sha256:" + "f" * 64


def _pod(reason: str | None, *, age_minutes: float, phase: str = "Pending") -> SimpleNamespace:
    waiting = SimpleNamespace(reason=reason, message="secret not found") if reason else None
    return SimpleNamespace(
        metadata=SimpleNamespace(
            creation_timestamp=datetime.now(UTC) - timedelta(minutes=age_minutes)
        ),
        status=SimpleNamespace(
            phase=phase,
            start_time=None,
            init_container_statuses=None,
            container_statuses=[
                SimpleNamespace(image=_IMAGE, state=SimpleNamespace(waiting=waiting))
            ],
        ),
    )


def _run(core: MagicMock, batch: MagicMock | None = None):
    batch = batch or MagicMock()
    client = SparkK8sClient(SparkK8sConfig(namespace="cryo"))
    with (
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core),
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch),
    ):
        return client.get_job_run("cryo-spark-x"), batch


def test_driver_stuck_pulling_past_grace_fails_and_cleans_up() -> None:
    core = MagicMock()
    core.read_namespaced_pod.return_value = _pod("ImagePullBackOff", age_minutes=5)

    result, batch = _run(core)

    assert result.state is JobRunState.FAILED
    assert f"{_IMAGE} could not be pulled: ImagePullBackOff" in (result.state_details or "")
    batch.delete_namespaced_job.assert_called_once()
    core.delete_namespaced_pod.assert_called_once_with(name="cryo-spark-x-driver", namespace="cryo")
    core.delete_namespaced_secret.assert_called_once()


def test_short_pull_error_within_grace_keeps_running() -> None:
    core = MagicMock()
    core.read_namespaced_pod.return_value = _pod("ErrImagePull", age_minutes=1)

    result, batch = _run(core)

    assert result.state is JobRunState.RUNNING
    batch.delete_namespaced_job.assert_not_called()


def test_container_creating_is_not_stuck() -> None:
    core = MagicMock()
    core.read_namespaced_pod.return_value = _pod("ContainerCreating", age_minutes=30)

    result, _ = _run(core)

    assert result.state is JobRunState.RUNNING


def test_wrapper_pod_stuck_before_driver_exists_fails() -> None:
    core = MagicMock()
    core.read_namespaced_pod.side_effect = ApiException(status=404)
    core.list_namespaced_pod.return_value = SimpleNamespace(
        items=[_pod("CreateContainerConfigError", age_minutes=4)]
    )
    batch = MagicMock()
    batch.read_namespaced_job.return_value = SimpleNamespace(
        status=SimpleNamespace(failed=None, succeeded=None), metadata=None
    )

    result, batch = _run(core, batch)

    assert result.state is JobRunState.FAILED
    assert "wrapper pod CreateContainerConfigError" in (result.state_details or "")
    batch.delete_namespaced_job.assert_called_once()
