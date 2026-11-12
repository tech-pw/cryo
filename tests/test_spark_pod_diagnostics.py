"""Pod waiting reasons and Warning events for a running Spark step (#488)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from kubernetes.client.exceptions import ApiException

from cryo.engines.spark.pod_diagnostics import pod_state, step_diagnostics

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def _pod(name, *, phase="Pending", reason=None, age_min=0.0, labels=None, conditions=None):
    waiting = SimpleNamespace(reason=reason, message=f"{reason} detail") if reason else None
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name, labels=labels or {}, creation_timestamp=NOW - timedelta(minutes=age_min)
        ),
        spec=SimpleNamespace(node_name="node-1"),
        status=SimpleNamespace(
            phase=phase,
            init_container_statuses=None,
            container_statuses=[SimpleNamespace(state=SimpleNamespace(waiting=waiting))],
            conditions=conditions or [],
        ),
    )


def test_image_pull_past_threshold_is_stuck() -> None:
    state = pod_state(_pod("p", reason="ImagePullBackOff", age_min=12), "driver", now=NOW)
    assert state.waiting_reason == "ImagePullBackOff"
    assert state.stuck
    assert round(state.waiting_seconds or 0) == 720


def test_running_pod_is_not_stuck() -> None:
    state = pod_state(_pod("p", phase="Running", age_min=60), "executor", now=NOW)
    assert not state.stuck
    assert state.waiting_seconds is None


def test_unschedulable_pending_pod_reports_the_condition() -> None:
    cond = SimpleNamespace(
        type="PodScheduled", status="False", reason="Unschedulable", message="Insufficient cpu"
    )
    state = pod_state(_pod("p", age_min=8, conditions=[cond]), "executor", now=NOW)
    assert state.waiting_reason == "Unschedulable"
    assert state.waiting_message == "Insufficient cpu"
    assert state.stuck


def test_step_diagnostics_reads_wrapper_driver_executors_and_events() -> None:
    core = MagicMock()
    driver = _pod("job-driver", phase="Running", labels={"spark-app-selector": "spark-123"})
    executor = _pod("job-exec-1", reason="ImagePullBackOff", age_min=10)
    core.list_namespaced_pod.side_effect = [
        SimpleNamespace(items=[_pod("job-abcde", phase="Running")]),
        SimpleNamespace(items=[executor]),
    ]
    core.read_namespaced_pod.return_value = driver
    event = SimpleNamespace(
        reason="Failed",
        message="Back-off pulling image",
        count=7,
        last_timestamp=NOW,
        event_time=None,
    )
    core.list_namespaced_event.side_effect = lambda **kw: SimpleNamespace(
        items=[event] if "job-exec-1" in kw["field_selector"] else []
    )

    result = step_diagnostics(core, "cryo", "job")

    assert [p.role for p in result.pods] == ["wrapper", "driver", "executor"]
    executor_call = core.list_namespaced_pod.call_args_list[1]
    assert (
        executor_call.kwargs["label_selector"] == "spark-app-selector=spark-123,spark-role=executor"
    )
    assert result.events[0].pod == "job-exec-1"
    assert result.events[0].count == 7
    assert result.summary is not None
    assert result.summary.startswith("Stuck: image pull failing for")


def test_missing_driver_and_forbidden_events_still_return_pods() -> None:
    core = MagicMock()
    core.list_namespaced_pod.return_value = SimpleNamespace(
        items=[_pod("job-abcde", phase="Running")]
    )
    core.read_namespaced_pod.side_effect = ApiException(status=404)
    core.list_namespaced_event.side_effect = ApiException(status=403)

    result = step_diagnostics(core, "cryo", "job")

    assert [p.role for p in result.pods] == ["wrapper"]
    assert result.events == []
