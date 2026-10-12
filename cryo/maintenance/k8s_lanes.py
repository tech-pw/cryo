"""Start, watch and tear down the Spark lane pods for one batch maintenance run.

One Kubernetes Job per lane rather than one Indexed Job: lanes are sized independently (a lane
of tiny tables gets 1 vCPU, a lane holding a multi-TB table gets 4), and an Indexed Job shares
one pod template across every index.

Each lane's table manifest travels in the same per-lane Secret as its S3 credentials. The
coordinator runs as the cryo-native ServiceAccount, which can already create and delete Secrets
and Jobs for the native engine, so no new RBAC is needed; a ConfigMap would have needed one.
"""

from __future__ import annotations

import base64
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from cryo.engines.k8s_cost_labels import cost_attribution_labels
from cryo.engines.k8s_security import container_security_context, pod_security_context
from cryo.engines.spark.k8s import (
    SparkK8sConfig,
    _new_k8s_batch_client,
    _new_k8s_core_client,
    catalog_io_confs,
)
from cryo.maintenance.results import LaneLog, StatementResult, parse_lane_log

logger = logging.getLogger(__name__)

RUN_LABEL = "cryo/maintenance-run"
LANE_LABEL = "cryo/maintenance-lane"
_CAPACITY_TYPE_LABEL = "karpenter.sh/capacity-type"
_MANIFEST_KEY = "manifest.json.gz"
_MANIFEST_MOUNT = "/manifest"
_RUNNER = "/opt/cryo/cryo_spark_runner.py"
# Headroom inside the pod limit for the Python runner and JVM off-heap on top of the heap.
# 0.7 left ~4.8 GiB on a 16 GiB lane and the kernel still OOM-killed one mid-rewrite.
_HEAP_FRACTION = 0.6
# Parquet codecs pin the heap through JNI critical sections. On Java 21's G1 a large
# allocation (vectored reads ask for whole column chunks) that hits a pinned heap gives up
# after 2 retries and throws OutOfMemoryError with most of the heap free.
_JAVA_OPTIONS = "-XX:+UnlockDiagnosticVMOptions -XX:GCLockerRetryAllocationCount=100"
# glibc gives each thread that mallocs its own arena; Spark's task + S3 pools grow native RSS
# far past the heap. Hadoop caps YARN containers at 4 for the same reason.
_MALLOC_ARENA_MAX = "4"
# local[N] runs every task exactly once: one S3 read timeout or connection reset fails the
# whole statement, and with it the rest of that table's plan. local[N,F] gives each task F
# attempts, the retry cluster-mode Spark always had (spark.task.maxFailures).
_LOCAL_TASK_MAX_FAILURES = 3
# A lane is one SparkContext that runs thousands of jobs over several hours. The status store
# keeps job/stage/task/SQL history for the UI whether or not the UI is enabled (defaults:
# 1000 jobs, 1000 stages, 100k tasks, 1000 SQL executions), all of it on the driver heap,
# which in local mode is the lane's only heap. Nothing reads that history here.
_STATUS_STORE_LIMITS = {
    "spark.ui.retainedJobs": "50",
    "spark.ui.retainedStages": "100",
    "spark.ui.retainedTasks": "5000",
    "spark.sql.ui.retainedExecutions": "20",
}


@dataclass(frozen=True)
class LaneSpec:
    index: int
    cpu_milli: int
    mem_mib: int
    manifest: bytes


@dataclass
class LaneState:
    spec: LaneSpec
    job_name: str
    secret_name: str
    # Keyed rather than positional: kubelet rotates container logs on a long lane, after
    # which the API returns only the newest file and line positions shift.
    seen: set[tuple[uuid.UUID, int]] = field(default_factory=set)
    started_tables: set[uuid.UUID] = field(default_factory=set)
    phase: str = "Pending"
    finished: bool = False
    lane_done: dict[str, Any] | None = None
    exit_reason: str | None = None
    # The node or kubelet took the pod away (eviction, drain, spot reclaim) rather than Spark
    # failing on its own. A replacement lane is worth starting even before the first table.
    infra_loss: bool = False


@dataclass
class PollUpdate:
    new_results: list[tuple[int, StatementResult]] = field(default_factory=list)
    newly_finished: list[LaneState] = field(default_factory=list)


def _short(run_id: uuid.UUID) -> str:
    return run_id.hex[:8]


def lane_names(run_id: uuid.UUID, index: int) -> str:
    return f"cryo-maint-{_short(run_id)}-{index}"


def parse_capacity_types(raw: str | None) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in (raw or "").split(",") if part.strip())


def lane_scheduling(
    node_selector: dict[str, str] | None,
    capacity_types: Sequence[str],
    *,
    max_node_cpu: int = 0,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    """(nodeSelector, affinity) for a lane pod.

    The Spark destination pins karpenter.sh/capacity-type=on-demand because a per-statement
    spark-submit run has nothing to resume from. A lane does: rewrites commit in partial-progress
    steps and the coordinator replaces a lane that dies, so lanes may take spot capacity. The
    inherited pin is swapped for a required In-list over ``capacity_types`` (Karpenter falls
    back to the next type when the first has no capacity) plus a preference for the first.
    Capacity selection stays unchanged without an inherited pin. The optional node CPU
    ceiling is independent and explicitly opts into Karpenter's instance-cpu labels.
    """
    selector = dict(node_selector or {})
    if max_node_cpu < 0:
        raise ValueError("max_node_cpu must be non-negative")
    expressions: list[dict[str, Any]] = []
    override_capacity = bool(capacity_types and _CAPACITY_TYPE_LABEL in selector)
    if override_capacity:
        del selector[_CAPACITY_TYPE_LABEL]
        expressions.append(
            {"key": _CAPACITY_TYPE_LABEL, "operator": "In", "values": list(capacity_types)}
        )
    if max_node_cpu:
        expressions.append(
            {
                "key": "karpenter.k8s.aws/instance-cpu",
                "operator": "Lt",
                "values": [str(max_node_cpu + 1)],
            }
        )
    if not expressions:
        return selector, None
    node_affinity: dict[str, Any] = {
        "requiredDuringSchedulingIgnoredDuringExecution": {
            "nodeSelectorTerms": [{"matchExpressions": expressions}]
        }
    }
    if override_capacity and len(capacity_types) > 1:
        node_affinity["preferredDuringSchedulingIgnoredDuringExecution"] = [
            {
                "weight": 100,
                "preference": {
                    "matchExpressions": [
                        {
                            "key": _CAPACITY_TYPE_LABEL,
                            "operator": "In",
                            "values": [capacity_types[0]],
                        }
                    ]
                },
            }
        ]
    return selector, {"nodeAffinity": node_affinity}


def spark_submit_command(
    spec: LaneSpec,
    cfg: SparkK8sConfig,
    *,
    catalog: str,
    deadline_epoch: float,
) -> list[str]:
    cores = max(1, spec.cpu_milli // 1000)
    heap_mib = max(1024, int(spec.mem_mib * _HEAP_FRACTION))
    confs = {
        "spark.ui.enabled": "false",
        "spark.sql.shuffle.partitions": str(max(4, cores * 4)),
        **_STATUS_STORE_LIMITS,
        **catalog_io_confs(catalog, cfg.catalog_warehouse),
    }
    command = [
        "/opt/spark/bin/spark-submit",
        "--master",
        f"local[{cores},{_LOCAL_TASK_MAX_FAILURES}]",
        "--driver-memory",
        f"{heap_mib}m",
        "--driver-java-options",
        _JAVA_OPTIONS,
    ]
    for key, value in confs.items():
        command += ["--conf", f"{key}={value}"]
    command += [
        _RUNNER,
        "--manifest",
        f"{_MANIFEST_MOUNT}/{_MANIFEST_KEY}",
        "--deadline-epoch",
        f"{deadline_epoch:.0f}",
        "--catalog",
        catalog,
        "--catalog-impl",
        cfg.catalog_impl,
    ]
    if cfg.catalog_uri:
        command += ["--catalog-uri", cfg.catalog_uri]
    if cfg.catalog_warehouse:
        command += ["--catalog-warehouse", cfg.catalog_warehouse]
    if cfg.s3_endpoint:
        command += ["--s3-endpoint", cfg.s3_endpoint]
    return command


def _validate_node_cpu_ceiling(cpu_milli: int, max_node_cpu: int) -> None:
    if max_node_cpu < 0:
        raise ValueError("max_node_cpu must be non-negative")
    if max_node_cpu and cpu_milli >= max_node_cpu * 1000:
        raise ValueError("Lane node CPU ceiling must leave room for system workloads")


def build_lane_objects(
    run_id: uuid.UUID,
    spec: LaneSpec,
    cfg: SparkK8sConfig,
    *,
    catalog: str,
    deadline_epoch: float,
    active_deadline_seconds: int,
    capacity_types: Sequence[str] = (),
    max_node_cpu: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(secret, job) manifests for one lane."""
    _validate_node_cpu_ceiling(spec.cpu_milli, max_node_cpu)
    name = lane_names(run_id, spec.index)
    labels = {
        "app.kubernetes.io/name": "cryo",
        "app.kubernetes.io/component": "maintenance-lane",
        RUN_LABEL: str(run_id),
        LANE_LABEL: str(spec.index),
        **cost_attribution_labels(
            workload_name="cryo-maintenance-lane", engine="spark", operation="batch"
        ),
    }
    secret_data = {_MANIFEST_KEY: base64.b64encode(spec.manifest).decode("ascii")}
    has_keys = bool(cfg.s3_access_key and cfg.s3_secret_key)
    if has_keys:
        secret_data["access_key_id"] = base64.b64encode(str(cfg.s3_access_key).encode()).decode()
        secret_data["secret_access_key"] = base64.b64encode(
            str(cfg.s3_secret_key).encode()
        ).decode()
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": cfg.namespace, "labels": labels},
        "type": "Opaque",
        "data": secret_data,
    }
    env: list[dict[str, Any]] = [{"name": "MALLOC_ARENA_MAX", "value": _MALLOC_ARENA_MAX}]
    if has_keys:
        # secretKeyRef rather than a literal value: pod specs are readable far more widely
        # than Secrets are.
        env += [
            {
                "name": "AWS_ACCESS_KEY_ID",
                "valueFrom": {"secretKeyRef": {"name": name, "key": "access_key_id"}},
            },
            {
                "name": "AWS_SECRET_ACCESS_KEY",
                "valueFrom": {"secretKeyRef": {"name": name, "key": "secret_access_key"}},
            },
        ]
    resources = {"cpu": f"{spec.cpu_milli}m", "memory": f"{spec.mem_mib}Mi"}
    node_selector, affinity = lane_scheduling(
        cfg.node_selector, capacity_types, max_node_cpu=max_node_cpu
    )
    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "serviceAccountName": cfg.service_account,
        "nodeSelector": node_selector,
        "imagePullSecrets": [{"name": s} for s in cfg.image_pull_secrets],
        "securityContext": pod_security_context(),
        "containers": [
            {
                "name": "spark",
                "image": cfg.image,
                "imagePullPolicy": cfg.image_pull_policy,
                "securityContext": container_security_context(),
                "command": spark_submit_command(
                    spec, cfg, catalog=catalog, deadline_epoch=deadline_epoch
                ),
                "env": env,
                "resources": {"requests": resources, "limits": resources},
                "volumeMounts": [
                    {"name": "manifest", "mountPath": _MANIFEST_MOUNT, "readOnly": True}
                ],
            }
        ],
        "volumes": [
            {
                "name": "manifest",
                "secret": {
                    "secretName": name,
                    "items": [{"key": _MANIFEST_KEY, "path": _MANIFEST_KEY}],
                },
            }
        ],
    }
    if affinity is not None:
        pod_spec["affinity"] = affinity
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": cfg.namespace, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": max(60, active_deadline_seconds),
            "ttlSecondsAfterFinished": 600,
            "template": {
                "metadata": {
                    "labels": labels,
                    # A lane holds commits in flight for hours; consolidation evicting it
                    # mid-rewrite wastes the whole lane's work.
                    "annotations": {"karpenter.sh/do-not-disrupt": "true"},
                },
                "spec": pod_spec,
            },
        },
    }
    return secret, job


class LaneRunner:
    def __init__(
        self,
        run_id: uuid.UUID,
        cfg: SparkK8sConfig,
        *,
        catalog: str,
        batch_api: Any | None = None,
        core_api: Any | None = None,
        capacity_types: Sequence[str] = (),
        max_node_cpu: int = 0,
    ) -> None:
        self._run_id = run_id
        self._cfg = cfg
        self._catalog = catalog
        self._batch = batch_api
        self._core = core_api
        self._capacity_types = tuple(capacity_types)
        self._max_node_cpu = max_node_cpu
        self.lanes: list[LaneState] = []

    def _batch_api(self) -> Any:
        if self._batch is None:
            self._batch = _new_k8s_batch_client()
        return self._batch

    def _core_api(self) -> Any:
        if self._core is None:
            self._core = _new_k8s_core_client()
        return self._core

    def launch(
        self, specs: Sequence[LaneSpec], *, deadline_epoch: float, active_deadline_seconds: int
    ) -> None:
        # Validate the whole plan before the first side effect: a later oversized lane
        # must not strand earlier pods when the coordinator has not received the runner.
        for spec in specs:
            _validate_node_cpu_ceiling(spec.cpu_milli, self._max_node_cpu)
        for spec in specs:
            secret, job = build_lane_objects(
                self._run_id,
                spec,
                self._cfg,
                catalog=self._catalog,
                deadline_epoch=deadline_epoch,
                active_deadline_seconds=active_deadline_seconds,
                capacity_types=self._capacity_types,
                max_node_cpu=self._max_node_cpu,
            )
            self._core_api().create_namespaced_secret(namespace=self._cfg.namespace, body=secret)
            self._batch_api().create_namespaced_job(namespace=self._cfg.namespace, body=job)
            name = job["metadata"]["name"]
            self.lanes.append(LaneState(spec=spec, job_name=name, secret_name=name))
            logger.info(
                "Launched lane %d (%s): %dm CPU / %dMi",
                spec.index,
                name,
                spec.cpu_milli,
                spec.mem_mib,
            )

    @property
    def all_finished(self) -> bool:
        return all(lane.finished for lane in self.lanes)

    def _lane_pod(self, lane: LaneState) -> Any | None:
        pods = self._core_api().list_namespaced_pod(
            namespace=self._cfg.namespace, label_selector=f"job-name={lane.job_name}"
        )
        items = list(getattr(pods, "items", None) or [])
        return items[0] if items else None

    def _read_log(self, pod_name: str) -> str:
        return str(
            self._core_api().read_namespaced_pod_log(
                name=pod_name, namespace=self._cfg.namespace, container="spark"
            )
            or ""
        )

    def poll(self) -> PollUpdate:
        update = PollUpdate()
        for lane in self.lanes:
            if lane.finished:
                continue
            pod = self._lane_pod(lane)
            if pod is None:
                # No pod: either the Job hasn't created it yet, or the pod was deleted out from
                # under us (eviction API, node drain, spot reclaim) and, with backoffLimit 0,
                # the Job failed without a replacement. Before 2026-09-25 the second case left
                # the lane "running" until the run's hard stop, stranding its tables.
                finished, reason, infra_loss = self._job_outcome(lane)
                if finished:
                    lane.finished = True
                    lane.exit_reason = reason
                    lane.infra_loss = infra_loss
                    update.newly_finished.append(lane)
                continue
            phase = str(getattr(pod.status, "phase", None) or "Pending")
            lane.phase = phase
            if phase in {"Running", "Succeeded", "Failed"}:
                try:
                    log = parse_lane_log(self._read_log(pod.metadata.name))
                except Exception:
                    logger.warning("Could not read log for lane %s", lane.job_name, exc_info=True)
                    log = None
                if log is not None:
                    self._consume(lane, log, update)
            if phase in {"Succeeded", "Failed"}:
                lane.finished = True
                if phase == "Failed":
                    lane.exit_reason = _termination_reason(pod)
                    lane.infra_loss = _is_infra_loss(pod)
                update.newly_finished.append(lane)
        return update

    def _job_outcome(self, lane: LaneState) -> tuple[bool, str | None, bool]:
        """(finished, failure reason, infra loss) for a lane that has no pod.

        (False, None, False) while the Job is still waiting to create its pod.
        """
        try:
            job = self._batch_api().read_namespaced_job(
                name=lane.job_name, namespace=self._cfg.namespace
            )
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return True, "lane Job and pod disappeared mid-run", True
            logger.warning("Could not read Job for lane %s", lane.job_name, exc_info=True)
            return False, None, False
        status = getattr(job, "status", None)
        failed_reason: str | None = None
        for condition in getattr(status, "conditions", None) or []:
            if getattr(condition, "type", None) == "Failed" and str(condition.status) == "True":
                failed_reason = str(getattr(condition, "reason", None) or "Failed")
                break
        if failed_reason is None and int(getattr(status, "failed", 0) or 0) > 0:
            failed_reason = "Failed"
        if failed_reason == "DeadlineExceeded":
            return True, "run deadline reached (lane Job DeadlineExceeded)", False
        if failed_reason is not None:
            return (
                True,
                "lane pod deleted mid-run (eviction, node drain or spot reclaim); "
                f"Job {failed_reason}",
                True,
            )
        if int(getattr(status, "succeeded", 0) or 0) > 0:
            return True, None, False
        return False, None, False

    @staticmethod
    def _consume(lane: LaneState, log: LaneLog, update: PollUpdate) -> None:
        for result in log.results:
            if result.key in lane.seen:
                continue
            lane.seen.add(result.key)
            update.new_results.append((lane.spec.index, result))
        lane.started_tables.update(log.started_tables)
        if log.lane_done is not None:
            lane.lane_done = log.lane_done

    def teardown(self) -> None:
        """Delete every Job, pod and Secret this run created, whatever state they are in."""
        namespace = self._cfg.namespace
        selector = f"{RUN_LABEL}={self._run_id}"
        for lane in self.lanes:
            _ignore_missing(
                lambda lane=lane: self._batch_api().delete_namespaced_job(
                    name=lane.job_name, namespace=namespace, propagation_policy="Background"
                )
            )
            _ignore_missing(
                lambda lane=lane: self._core_api().delete_namespaced_secret(
                    name=lane.secret_name, namespace=namespace
                )
            )
        # Per-pod deletes: the cryo-native Role grants pods/delete but not deletecollection.
        try:
            pods = self._core_api().list_namespaced_pod(
                namespace=namespace, label_selector=selector
            )
        except Exception:
            logger.warning("Could not list lane pods for cleanup", exc_info=True)
            return
        for pod in getattr(pods, "items", None) or []:
            _ignore_missing(
                lambda pod=pod: self._core_api().delete_namespaced_pod(
                    name=pod.metadata.name, namespace=namespace, grace_period_seconds=0
                )
            )


def _termination_reason(pod: Any) -> str | None:
    for status in getattr(pod.status, "container_statuses", None) or []:
        terminated = getattr(getattr(status, "state", None), "terminated", None)
        if terminated is not None:
            reason = getattr(terminated, "reason", None) or "Error"
            code = getattr(terminated, "exit_code", None)
            return f"{reason} (exit {code})"
    return getattr(pod.status, "reason", None)


# Pod-level reasons the kubelet or node lifecycle sets when it, not Spark, ended the pod.
_INFRA_POD_REASONS = frozenset(
    {"Evicted", "NodeShutdown", "NodeLost", "Shutdown", "Terminated", "UnexpectedAdmissionError"}
)


def _is_infra_loss(pod: Any) -> bool:
    status = getattr(pod, "status", None)
    if getattr(status, "reason", None) in _INFRA_POD_REASONS:
        return True
    # Set on pods killed by the eviction API, taint manager, kubelet or scheduler preemption.
    return any(
        getattr(condition, "type", None) == "DisruptionTarget"
        and str(getattr(condition, "status", "")) == "True"
        for condition in getattr(status, "conditions", None) or []
    )


def _ignore_missing(call: Any) -> None:
    try:
        call()
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return
        logger.warning("Lane cleanup call failed", exc_info=True)
