"""Pod state and Warning events for one running Spark-on-Kubernetes step (#488).

Read on demand for a single job, never for the fleet: the wrapper Job's pods, the driver,
up to ``_MAX_EXECUTORS`` executors, and the Warning events on those pods.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

_DRIVER_SUFFIX = "-driver"
_MAX_EXECUTORS = 20
_MAX_EVENTS_PER_POD = 5
# A pod waiting this long gets a severity chip.
STUCK_AFTER_SECONDS = 300
# Reasons that mean the pod is not computing yet.
_WAITING_REASONS = frozenset(
    {
        "ErrImagePull",
        "ImagePullBackOff",
        "InvalidImageName",
        "CreateContainerConfigError",
        "CreateContainerError",
        "ContainerCreating",
        "PodInitializing",
        "CrashLoopBackOff",
    }
)


@dataclass(frozen=True)
class PodState:
    role: str
    name: str
    phase: str | None
    waiting_reason: str | None
    waiting_message: str | None
    waiting_seconds: float | None
    node: str | None
    stuck: bool


@dataclass(frozen=True)
class PodEvent:
    pod: str
    reason: str | None
    message: str | None
    count: int
    last_seen: str | None


@dataclass(frozen=True)
class StepDiagnostics:
    engine_ref: str
    pods: list[PodState] = field(default_factory=list)
    events: list[PodEvent] = field(default_factory=list)
    summary: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def pod_state(pod: Any, role: str, *, now: datetime | None = None) -> PodState:
    current = now or datetime.now(UTC)
    status = getattr(pod, "status", None)
    metadata = getattr(pod, "metadata", None)
    phase = getattr(status, "phase", None)
    reason = message = None
    statuses = [
        *(getattr(status, "init_container_statuses", None) or []),
        *(getattr(status, "container_statuses", None) or []),
    ]
    for container in statuses:
        waiting = getattr(getattr(container, "state", None), "waiting", None)
        if waiting is not None and getattr(waiting, "reason", None):
            reason, message = waiting.reason, getattr(waiting, "message", None)
            break
    if reason is None and phase == "Pending":
        for condition in getattr(status, "conditions", None) or []:
            if (
                getattr(condition, "type", None) == "PodScheduled"
                and getattr(condition, "status", None) == "False"
            ):
                reason = getattr(condition, "reason", None) or "Unschedulable"
                message = getattr(condition, "message", None)
    created = _aware(getattr(metadata, "creation_timestamp", None))
    waiting_seconds = (
        (current - created).total_seconds()
        if created is not None and (reason is not None or phase == "Pending")
        else None
    )
    not_computing = phase == "Pending" or reason in _WAITING_REASONS
    return PodState(
        role=role,
        name=str(getattr(metadata, "name", "")),
        phase=phase,
        waiting_reason=reason,
        waiting_message=message,
        waiting_seconds=waiting_seconds,
        node=getattr(getattr(pod, "spec", None), "node_name", None),
        stuck=bool(not_computing and waiting_seconds and waiting_seconds >= STUCK_AFTER_SECONDS),
    )


def _summary(pods: list[PodState]) -> str | None:
    stuck = [p for p in pods if p.stuck]
    if not stuck:
        return None
    worst = max(stuck, key=lambda p: p.waiting_seconds or 0)
    minutes = int((worst.waiting_seconds or 0) // 60)
    reason = worst.waiting_reason or worst.phase or "waiting"
    if reason in {"ErrImagePull", "ImagePullBackOff", "InvalidImageName"}:
        return f"Stuck: image pull failing for {minutes}m ({worst.role})"
    return f"Stuck: {worst.role} {reason} for {minutes}m"


def step_diagnostics(core_api: Any, namespace: str, engine_ref: str) -> StepDiagnostics:
    from kubernetes.client.exceptions import ApiException

    now = datetime.now(UTC)
    pods: list[tuple[str, Any]] = []
    wrapper = core_api.list_namespaced_pod(
        namespace=namespace, label_selector=f"job-name={engine_ref}", limit=5
    )
    pods.extend(("wrapper", pod) for pod in wrapper.items or [])
    driver = None
    try:
        driver = core_api.read_namespaced_pod(
            name=f"{engine_ref}{_DRIVER_SUFFIX}", namespace=namespace
        )
        pods.append(("driver", driver))
    except ApiException as exc:
        if exc.status != 404:
            raise
    app_selector = ((getattr(driver, "metadata", None) and driver.metadata.labels) or {}).get(
        "spark-app-selector"
    )
    if app_selector:
        executors = core_api.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"spark-app-selector={app_selector},spark-role=executor",
            limit=_MAX_EXECUTORS,
        )
        pods.extend(("executor", pod) for pod in executors.items or [])
    states = [pod_state(pod, role, now=now) for role, pod in pods]
    events: list[PodEvent] = []
    for state in states:
        try:
            listed = core_api.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={state.name},type=Warning",
                limit=50,
            )
        except ApiException as exc:
            if exc.status in {401, 403}:
                break
            raise
        recent = sorted(
            listed.items or [],
            key=lambda e: (
                _aware(getattr(e, "last_timestamp", None) or getattr(e, "event_time", None))
                or datetime.min.replace(tzinfo=UTC)
            ),
            reverse=True,
        )[:_MAX_EVENTS_PER_POD]
        for event in recent:
            seen = _aware(
                getattr(event, "last_timestamp", None) or getattr(event, "event_time", None)
            )
            events.append(
                PodEvent(
                    pod=state.name,
                    reason=getattr(event, "reason", None),
                    message=getattr(event, "message", None),
                    count=int(getattr(event, "count", None) or 1),
                    last_seen=seen.isoformat() if seen else None,
                )
            )
    return StepDiagnostics(
        engine_ref=engine_ref, pods=states, events=events, summary=_summary(states)
    )
