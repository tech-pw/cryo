"""Kubernetes Job-based dispatch for cryo-engine binaries.

Jobs are created on Governor admit (preferred path) or via the legacy
blocking Worker path (``execute_request_via_job``). Resource requests are
sized by ``cryo.engines.native.resources`` and hard-capped at 16Gi memory.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kubernetes import client

from cryo.engines.k8s_cost_labels import cost_attribution_labels, operation_from_native_payload
from cryo.engines.k8s_security import container_security_context, pod_security_context
from cryo.engines.native.job_state import current_k8s_namespace
from cryo.engines.native.resources import (
    MAX_POD_CPU_MILLI,
    MAX_POD_MEM_MIB,
    ResourceCeilings,
    chunk_size_gb_for_pod_mem,
    compute_job_resources,
)
from cryo.engines.native.runner import (
    NativeExecuteResult,
    NativeOperationResult,
    split_stdout_lines,
)

logger = logging.getLogger(__name__)

_MAX_CPU_MILLI = MAX_POD_CPU_MILLI
_MAX_MEM_MIB = MAX_POD_MEM_MIB
# Raised 7200 -> 14400 (2h -> 4h), , 2026-08-19: this was not a generous ceiling with
# a rare tail miss -- it was routinely marginal. Live query against Wave 1's () real
# job history: even *successful* 32-table batches (max_tables_per_pod) commonly finished at
# 114-132 minutes, right up against the old 120-minute deadline (421 succeeded at exactly 120
# min alone). 98% of the 5,553 native_job_failed failures in that same cohort clustered in the
# same 120-130 minute band -- these were batches that would very likely have succeeded given a
# few more minutes, not genuine per-table failures. Worse: K8s deletes a Job's pod immediately
# on activeDeadlineSeconds (unlike a normal exit-code failure, where the pod lingers for log
# collection), so every table in a killed batch lost its diagnostic trail and any of its own
# members that had already finished -- see cryo.engines.native.watcher's failure path and the
# run_events salvage added alongside this change. 4h gives real margin against the observed
# 114-132min real-world spread without materially changing per-table throughput (the batch
# still returns as soon as all members finish; this only raises the hard ceiling).
_ACTIVE_DEADLINE_SECONDS = 14400
# Follow-up, 2026-08-20: the 4h raise above bought real margin but batches were still
# hitting native_job_failed at close to the same rate as before -- confirmed live, still-running
# 32-table pods observed at 1 millicore CPU / 76MB memory two hours in, i.e. genuinely hung, not
# slow. Root cause: engine-go's Hive Metastore connect path (gohive.ConnectToMetastore, wrapped
# by third_party/iceberg-go-v0.6.0-patched/catalog/hive/client.go) had no socket timeout at all,
# so a single catalog.Load attempt against a stale/black-holed metastore IP (DNS-rotated NLB
# backend, not a Cryo-side cache issue) could block for ~27.7 minutes per attempt -- confirmed
# live via pod logs, 4 retries * ~27.7min ≈ 111min lost to two bad tables alone in one sampled
# batch. The 114-132min "successful" baseline this comment originally cited was real per-table
# processing time; the failures were a different, previously-invisible failure mode wearing the
# same activeDeadlineSeconds symptom. Fixed at the source (connectWithTimeout, 45s cap per
# attempt) rather than by raising this deadline further -- a longer deadline only delays a hang
# that should instead fail fast and retry, per catalogLoadAttempts' own design intent.
#
# Follow-up, 2026-08-24: the 45s cap stopped the unbounded hang, but 739 job failures in 24h
# still showed every one of the 4 attempts burning the full 45s against a sustained metastore
# outage (not the transient, fails-fast-most-of-the-time case catalogLoadAttempts=4 was tuned
# for) -- averaging 4,103s (68min), max 14,424s (4h) per table, and since batch pods run tables
# fully serially (batch.py's execute_batch), a 32-table batch hitting this could burn well over
# an hour before reaching its healthy tables. Fixed at the retry layer: loadCatalogWithRetry now
# stops after the first bounded-timeout error instead of spending the remaining 3 attempts on a
# failure a timeout has already strongly signaled won't recover within this call (see
# isMetastoreUnreachable in engine-go/internal/catalog/catalog.go). Fast/transient errors
# (Thrift busy, connection refused) still retry the full 4 attempts as before.
_DEFAULT_GB_PER_HOUR = 40.0

__all__ = [
    "JobCreateResult",
    "JobDispatchResult",
    "compute_job_resources",
    "create_native_job",
    "estimate_duration_seconds",
    "execute_request_via_job",
    "k8s_dispatch_available",
    "resolve_k8s_namespace",
    "spot_allowed_for_job",
]


@dataclass(frozen=True)
class JobDispatchResult:
    ok: bool
    stdout: str | None
    stderr: str | None
    job_name: str | None
    error: str | None


@dataclass(frozen=True)
class JobCreateResult:
    ok: bool
    job_name: str | None
    cpu_milli: int
    mem_mib: int
    error: str | None = None


def k8s_dispatch_available() -> bool:
    """True when the kubernetes client loads and in-cluster config is present."""
    k8s_client, _ = _safe_load_k8s()
    if k8s_client is None:
        return False
    return _in_cluster_configured()


def resolve_k8s_namespace(config: Mapping[str, Any]) -> str:
    current = current_k8s_namespace()
    configured = config.get("k8s_namespace")
    if isinstance(configured, str) and configured.strip():
        requested = configured.strip()
        if requested != current:
            logger.warning(
                "Ignoring cross-namespace native Job request for %s; Governor namespace is %s",
                requested,
                current,
            )
        return current
    return current


def estimate_duration_seconds(
    chunk_size_gb: float, *, gb_per_hour: float = _DEFAULT_GB_PER_HOUR
) -> float:
    """Best-effort wall-clock estimate used only for spot gating."""
    rate = max(float(gb_per_hour), 1.0)
    return max(60.0, (float(chunk_size_gb) / rate) * 3600.0)


def spot_allowed_for_job(
    *,
    chunk_size_gb: float,
    estimated_duration_seconds: float | None = None,
    config: Mapping[str, Any] | None = None,
    checkpointing_enabled: bool | None = None,
) -> bool:
    """Return True only when spot capacity is safe for this chunk.

    Policy (native-engine-scoping-research.md §6.5):
    - Spot must be explicitly allowed (config/env).
    - Chunk size and estimated duration must be under configured caps.
    - When require_checkpointing is set, checkpointing must be enabled.
    """
    cfg = dict(config or {})
    allow = _bool_cfg(cfg, "k8s_spot_allow", "CRYO_NATIVE_SPOT_ALLOW", default=False)
    if not allow:
        return False

    max_gb = _float_cfg(cfg, "k8s_spot_max_chunk_gb", "CRYO_NATIVE_SPOT_MAX_CHUNK_GB", default=50.0)
    max_duration = _float_cfg(
        cfg,
        "k8s_spot_max_estimated_duration_seconds",
        "CRYO_NATIVE_SPOT_MAX_DURATION_SECONDS",
        default=1800.0,
    )
    require_ckpt = _bool_cfg(
        cfg,
        "k8s_spot_require_checkpointing",
        "CRYO_NATIVE_SPOT_REQUIRE_CHECKPOINTING",
        default=True,
    )
    if checkpointing_enabled is None:
        checkpointing_enabled = _bool_cfg(
            cfg,
            "partial_progress_enabled",
            "CRYO_NATIVE_CHECKPOINTING",
            default=True,
        )

    duration = (
        float(estimated_duration_seconds)
        if estimated_duration_seconds is not None
        else estimate_duration_seconds(chunk_size_gb)
    )
    if float(chunk_size_gb) > max_gb:
        return False
    if duration > max_duration:
        return False
    if require_ckpt and not checkpointing_enabled:
        return False
    return True


def _bool_cfg(cfg: Mapping[str, Any], key: str, env_key: str, *, default: bool) -> bool:
    if key in cfg and cfg[key] is not None:
        value = cfg[key]
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _float_cfg(cfg: Mapping[str, Any], key: str, env_key: str, *, default: float) -> float:
    if key in cfg and cfg[key] is not None:
        return float(cfg[key])
    env_val = os.environ.get(env_key)
    if env_val is not None and env_val.strip():
        return float(env_val)
    return default


def _capacity_scheduling(
    *,
    use_spot: bool,
    dedicated_nodepool: bool = False,
) -> tuple[dict[str, str], list[dict[str, str]], dict[str, str]]:
    """Return (node_selector extras, tolerations, annotations) for capacity type.

    ``dedicated_nodepool`` opts into the cryo.io/workload=native-engine label +
    taint pairing (see deploy/helm/cryo/templates/karpenter-nodepool.yaml) so
    these Jobs land on their own Karpenter-managed nodes instead of sharing
    the default node pool -- off by default so existing scheduling is
    unaffected until that NodePool is actually installed for a cluster.
    """
    annotations = {
        "cryo.io/capacity-type": "spot" if use_spot else "on-demand",
    }
    node_selector: dict[str, str] = {
        "karpenter.sh/capacity-type": "spot" if use_spot else "on-demand"
    }
    tolerations: list[dict[str, str]] = []
    if not use_spot:
        annotations["karpenter.sh/do-not-disrupt"] = "true"
    else:
        tolerations.append(
            {
                "key": "karpenter.sh/capacity-type",
                "operator": "Equal",
                "value": "spot",
                "effect": "NoSchedule",
            }
        )
    if dedicated_nodepool:
        node_selector["cryo.io/workload"] = "native-engine"
        tolerations.append(
            {
                "key": "cryo.io/workload",
                "operator": "Equal",
                "value": "native-engine",
                "effect": "NoSchedule",
            }
        )
    return (node_selector, tolerations, annotations)


def _resolve_image_pull_secrets(config: Mapping[str, Any]) -> list[str]:
    raw = config.get("k8s_image_pull_secrets") or os.environ.get("CRYO_NATIVE_IMAGE_PULL_SECRETS")
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(s) for s in raw]
    return []


def create_native_job(
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    cpu_milli: int,
    mem_mib: int,
    job_name: str | None = None,
    dispatch_id: str | None = None,
    run_id: str | None = None,
    table_refs: Sequence[str] | None = None,
    timeout_seconds: int | None = None,
    command: Sequence[str] | None = None,
) -> JobCreateResult:
    """Create a Kubernetes Job without waiting for completion.

    Memory is hard-clamped to 16Gi. Falls back is the caller's responsibility
    when ``k8s_dispatch_available()`` is False.
    """
    ceilings = ResourceCeilings.from_config(config)
    cpu_milli = min(max(250, int(cpu_milli)), ceilings.max_cpu_milli, MAX_POD_CPU_MILLI)
    mem_mib = min(max(512, int(mem_mib)), ceilings.max_mem_mib, MAX_POD_MEM_MIB)

    k8s_client, _ = _safe_load_k8s()
    if k8s_client is None:
        return JobCreateResult(
            ok=False,
            job_name=None,
            cpu_milli=cpu_milli,
            mem_mib=mem_mib,
            error="kubernetes client not available",
        )
    if not _in_cluster_configured():
        return JobCreateResult(
            ok=False,
            job_name=None,
            cpu_milli=cpu_milli,
            mem_mib=mem_mib,
            error="not running in-cluster",
        )

    if job_name is None:
        job_name = f"cryo-native-{uuid.uuid4().hex[:8]}"

    payload_json = json.dumps(dict(payload))
    payload_b64 = base64.b64encode(payload_json.encode()).decode()
    timeout_seconds = timeout_seconds or int(
        config.get("k8s_active_deadline_seconds", _ACTIVE_DEADLINE_SECONDS)
    )

    image = str(config.get("k8s_image") or os.environ.get("CRYO_NATIVE_IMAGE") or "cryo:latest")
    service_account = str(
        config.get("k8s_service_account")
        or os.environ.get("CRYO_NATIVE_SERVICE_ACCOUNT")
        or "default"
    )
    ttl_seconds = int(
        config.get("k8s_ttl_seconds_after_finished")
        or os.environ.get("CRYO_NATIVE_TTL_SECONDS")
        or 3600
    )
    image_pull_policy = str(
        config.get("k8s_image_pull_policy")
        or os.environ.get("CRYO_NATIVE_IMAGE_PULL_POLICY")
        or "IfNotPresent"
    )
    image_pull_secrets = _resolve_image_pull_secrets(config)
    namespace = resolve_k8s_namespace(config)
    destination_id = str(config.get("destination_id") or config.get("id") or "")
    binary = str(config.get("binary_path", "/usr/local/bin/cryo-engine-go"))

    # Spot gating (scoping §6.5): estimate a chunk size from the pod's own memory
    # request (same inversion native_admit uses to derive the planner's chunk_size_gb)
    # so the decision reflects actual job size even when the caller didn't pass one.
    estimated_chunk_gb = chunk_size_gb_for_pod_mem(mem_mib)
    use_spot = spot_allowed_for_job(chunk_size_gb=estimated_chunk_gb, config=config)
    dedicated_nodepool = _bool_cfg(
        dict(config), "k8s_dedicated_nodepool", "CRYO_NATIVE_DEDICATED_NODEPOOL", default=False
    )
    node_extras, tolerations, capacity_annotations = _capacity_scheduling(
        use_spot=use_spot, dedicated_nodepool=dedicated_nodepool
    )
    if not use_spot and _bool_cfg(
        dict(config), "k8s_spot_allow", "CRYO_NATIVE_SPOT_ALLOW", default=False
    ):
        logger.info(
            "Spot requested but gated off for chunk_size_gb=%.1f; using on-demand",
            estimated_chunk_gb,
        )
    node_selector: dict[str, str] = {"kubernetes.io/arch": "amd64"}
    node_selector.update(node_extras)

    if command is not None:
        # Caller-supplied entrypoint (e.g. scan Jobs via cryo.scanner.job_dispatch) --
        # reuses this manifest/Secret/scheduling machinery with a different container
        # command instead of the native engine binary.
        command = list(command)
    else:
        # Prefer Python batch entrypoint so multi-table serial pods work without
        # changing the Go binary contract. Single-table batches still work.
        use_batch_entrypoint = bool(payload.get("batch")) or bool(
            config.get("k8s_batch_entrypoint", True)
        )
        if use_batch_entrypoint:
            command = ["python", "-m", "cryo.engines.native.batch"]
        else:
            command = [binary, "execute"]

    primary_ref = "batch"
    if table_refs:
        primary_ref = str(table_refs[0])[:63]
    elif isinstance(payload.get("table_ref"), str):
        primary_ref = str(payload["table_ref"])[:63]

    workload_name, engine_name, operation = operation_from_native_payload(payload, command=command)
    labels = {
        "app.kubernetes.io/name": "cryo",
        "app.kubernetes.io/component": "native-engine",
        "app": "cryo-native",
        "cryo/destination": (destination_id or "default")[:63],
        **cost_attribution_labels(
            workload_name=workload_name, engine=engine_name, operation=operation
        ),
    }
    if dispatch_id:
        labels["cryo/dispatch-id"] = str(dispatch_id)[:63]
    if run_id:
        labels["cryo/run-id"] = str(run_id)[:63]

    annotations = {
        "cryo/cpu": f"{cpu_milli}m",
        "cryo/memory": f"{mem_mib}Mi",
        "cryo/table_ref": primary_ref,
        **capacity_annotations,
    }
    payload_secret_name = f"{job_name}-payload"

    job_manifest: dict[str, Any] = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": labels,
            "annotations": annotations,
        },
        "spec": {
            "ttlSecondsAfterFinished": ttl_seconds,
            "activeDeadlineSeconds": timeout_seconds,
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": dict(labels),
                    "annotations": dict(capacity_annotations),
                },
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": service_account,
                    "nodeSelector": node_selector,
                    "tolerations": tolerations,
                    "imagePullSecrets": [{"name": s} for s in image_pull_secrets],
                    "securityContext": pod_security_context(),
                    "containers": [
                        {
                            "name": "cryo-engine",
                            "image": image,
                            "imagePullPolicy": image_pull_policy,
                            "securityContext": container_security_context(),
                            "command": command,
                            "env": [
                                # File path, not the payload itself -- see volumeMounts below.
                                # Linux caps any single argv/env string at MAX_ARG_STRLEN
                                # (128KiB); a large table's batch payload (e.g. a table with
                                # 200k+ files) blows past that as base64 and the container
                                # fails before Python even starts: "exec /usr/local/bin/python:
                                # argument list too long". Confirmed live, 2026-08-07, on a
                                # 211,158-file table. A mounted Secret file has no such
                                # exec-time size limit (bounded only by the Secret's own 1MiB
                                # etcd cap, ~8x more headroom than the env var path had).
                                {"name": "CRYO_PAYLOAD_FILE", "value": "/etc/cryo/payload/payload"},
                                {"name": "CRYO_ENGINE_BINARY", "value": binary},
                            ],
                            "volumeMounts": [
                                {
                                    "name": "payload",
                                    "mountPath": "/etc/cryo/payload",
                                    "readOnly": True,
                                },
                            ],
                            "resources": {
                                "requests": {
                                    "cpu": f"{cpu_milli}m",
                                    "memory": f"{mem_mib}Mi",
                                },
                                "limits": {
                                    "cpu": f"{min(cpu_milli * 2, MAX_POD_CPU_MILLI)}m",
                                    "memory": f"{min(mem_mib * 2, MAX_POD_MEM_MIB)}Mi",
                                },
                            },
                        },
                    ],
                    "volumes": [
                        {
                            "name": "payload",
                            "secret": {
                                "secretName": payload_secret_name,
                                "items": [{"key": "payload", "path": "payload"}],
                            },
                        },
                    ],
                },
            },
        },
    }

    batch_api = _new_k8s_batch_client()
    core_api = _new_k8s_core_client()
    secret_created = False
    try:
        core_api.create_namespaced_secret(
            namespace=namespace,
            body={
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": payload_secret_name,
                    "namespace": namespace,
                    "labels": labels,
                },
                "type": "Opaque",
                "stringData": {"payload": payload_b64},
            },
        )
        secret_created = True
        created_job = batch_api.create_namespaced_job(body=job_manifest, namespace=namespace)
        _adopt_payload_secret(
            core_api,
            secret_name=payload_secret_name,
            namespace=namespace,
            job_name=job_name,
            created_job=created_job,
        )
        logger.info(
            "Created Kubernetes Job %s (cpu=%dm mem=%dMi spot=%s dispatch=%s)",
            job_name,
            cpu_milli,
            mem_mib,
            use_spot,
            dispatch_id,
        )
        return JobCreateResult(
            ok=True,
            job_name=job_name,
            cpu_milli=cpu_milli,
            mem_mib=mem_mib,
        )
    except Exception as exc:
        if secret_created:
            try:
                core_api.delete_namespaced_secret(name=payload_secret_name, namespace=namespace)
            except Exception:
                pass
        logger.error("Failed to create Job %s: %s", job_name, exc)
        return JobCreateResult(
            ok=False,
            job_name=job_name,
            cpu_milli=cpu_milli,
            mem_mib=mem_mib,
            error=f"Failed to create Kubernetes Job: {exc}",
        )


def execute_request_via_job(
    request: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    job_name: str | None = None,
    chunk_size_gb: float | None = None,
    cpu_milli: int | None = None,
    mem_mib: int | None = None,
    timeout_seconds: int | None = None,
) -> NativeExecuteResult:
    """Legacy blocking path: create Job and poll until done (Worker fallback)."""
    if not k8s_dispatch_available():
        logger.warning("kubernetes unavailable, falling back to subprocess")
        return _fallback_subprocess(request, config, timeout_seconds)

    table_ref = str(request.get("table_ref", "unknown"))
    ceilings = ResourceCeilings.from_config(config)
    if cpu_milli is None or mem_mib is None:
        size_hint = float(chunk_size_gb) if chunk_size_gb is not None else 100.0
        sized_cpu, sized_mem = compute_job_resources(size_hint, ceilings=ceilings)
        cpu_milli = cpu_milli if cpu_milli is not None else sized_cpu
        mem_mib = mem_mib if mem_mib is not None else sized_mem
    created = create_native_job(
        request,
        config={**dict(config), "k8s_batch_entrypoint": False},
        cpu_milli=cpu_milli,
        mem_mib=mem_mib,
        job_name=job_name,
        table_refs=(table_ref,),
        timeout_seconds=timeout_seconds,
    )
    if not created.ok or not created.job_name:
        return NativeExecuteResult(
            ok=False,
            table_ref=table_ref,
            results=(),
            error=created.error or "Failed to create Kubernetes Job",
        )

    return _poll_job_until_done(
        created.job_name,
        table_ref=table_ref,
        config=config,
        timeout_seconds=timeout_seconds or _ACTIVE_DEADLINE_SECONDS,
    )


def _poll_job_until_done(
    job_name: str,
    *,
    table_ref: str,
    config: Mapping[str, Any],
    timeout_seconds: int,
) -> NativeExecuteResult:
    k8s_client, _ = _safe_load_k8s()
    namespace = resolve_k8s_namespace(config)
    batch_api = _new_k8s_batch_client()
    poll_interval = 5.0
    deadline = time.monotonic() + timeout_seconds

    try:
        while time.monotonic() < deadline:
            job = batch_api.read_namespaced_job(name=job_name, namespace=namespace)
            status = job.status

            if status.succeeded is not None and status.succeeded >= 1:
                pods = _get_job_pods(batch_api, job_name, namespace)
                stdout_text = _read_pod_logs(pods, "cryo-engine", k8s_client)
                return _parse_job_result(stdout_text, table_ref, job_name)

            if status.failed is not None and status.failed >= 1:
                pods = _get_job_pods(batch_api, job_name, namespace)
                stderr_text = _read_pod_logs(pods, "cryo-engine", k8s_client)
                return NativeExecuteResult(
                    ok=False,
                    table_ref=table_ref,
                    results=(),
                    error=f"Job {job_name} failed",
                    stderr=stderr_text,
                )

            time.sleep(poll_interval)

        batch_api.delete_namespaced_job(name=job_name, namespace=namespace)
        return NativeExecuteResult(
            ok=False,
            table_ref=table_ref,
            results=(),
            error=f"Job {job_name} timed out after {timeout_seconds}s",
        )

    except Exception as exc:
        try:
            batch_api.delete_namespaced_job(name=job_name, namespace=namespace)
        except Exception:
            pass
        return NativeExecuteResult(
            ok=False,
            table_ref=table_ref,
            results=(),
            error=f"Job {job_name} error during polling: {exc}",
        )


def _safe_load_k8s() -> tuple[type, type] | tuple[None, None]:
    try:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config

        return k8s_client, k8s_config
    except ImportError:
        return None, None


def _in_cluster_configured() -> bool:
    k8s_client, k8s_config = _safe_load_k8s()
    if k8s_client is None:
        return False
    try:
        k8s_config.load_incluster_config()  # type: ignore[union-attr]
        return True
    except Exception:
        return False


def _new_k8s_batch_client() -> client.BatchV1Api:
    from kubernetes import client

    return client.BatchV1Api()


def _new_k8s_core_client() -> client.CoreV1Api:
    from kubernetes import client

    return client.CoreV1Api()


def _adopt_payload_secret(
    core_api: Any,
    *,
    secret_name: str,
    namespace: str,
    job_name: str,
    created_job: Any,
) -> None:
    metadata = getattr(created_job, "metadata", None)
    uid = getattr(metadata, "uid", None)
    if not uid:
        logger.warning("Created Job %s without UID; payload Secret needs watcher cleanup", job_name)
        return
    try:
        core_api.patch_namespaced_secret(
            name=secret_name,
            namespace=namespace,
            body={
                "metadata": {
                    "ownerReferences": [
                        {
                            "apiVersion": "batch/v1",
                            "kind": "Job",
                            "name": job_name,
                            "uid": str(uid),
                            "controller": True,
                            "blockOwnerDeletion": True,
                        }
                    ]
                }
            },
        )
    except Exception:
        logger.warning("Failed to attach payload Secret to Job %s", job_name)


def _get_job_pods(
    batch_api: client.BatchV1Api,
    job_name: str,
    namespace: str,
) -> list[client.V1Pod]:
    from kubernetes import client

    core_api = client.CoreV1Api()
    label_selector = f"job-name={job_name}"
    try:
        pods = core_api.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
        )
        return pods.items  # type: ignore[no-any-return]
    except Exception:
        return []


def _read_pod_logs(
    pods: list[client.V1Pod],
    container_name: str,
    k8s_client: type | None,
) -> str | None:
    """Read a completed pod's full stdout so every batched table's result line is seen.

    Deliberately passes neither `limit_bytes` nor `tail_lines`. Same regression class
    already found and fixed in cryo/engines/spark/k8s.py::_read_pod_log_tail on
    2026-07-31: Kubernetes' `limit_bytes` truncates from the START of the log stream,
    not the end -- the old `limit_bytes=1024*512` here silently dropped every
    CRYO_TABLE_RESULT line that landed after the first 512KiB. That's a routine
    outcome, not an edge case: packing.py allows up to 32 tables serially in one
    shared batch pod (`max_tables_per_pod`), and NativeJobWatcher._finalize_success
    treats any job whose result line got truncated out as `native_job_no_result` --
    silently marking a table FAILED (or losing its real error) even when the batch
    Job itself succeeded and that table's own operation genuinely completed.
    Unlike Spark's error-tail use (`tail_lines=200`, only the last error matters), a
    tail alone isn't sufficient here either -- a batch's result lines are scattered
    across the whole log, one per table, so the finalization path needs the complete
    log, not a fixed head or tail slice. This is bounded anyway: the pod already
    exited, so the log is static, and kubelet's own container-log rotation (not this
    client) is the real ceiling on how much is even retained.
    """
    if not pods:
        return None
    from kubernetes import client

    core_api = client.CoreV1Api()
    for pod in pods:
        try:
            logs: str = core_api.read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=pod.metadata.namespace,
                container=container_name,
            )
            return logs
        except Exception:
            continue
    return None


def _parse_job_result(
    stdout_text: str | None, table_ref: str, job_name: str
) -> NativeExecuteResult:
    if not stdout_text:
        return NativeExecuteResult(
            ok=False,
            table_ref=table_ref,
            results=(),
            error=f"Job {job_name} produced no output",
            stderr=None,
        )

    _, response_lines = split_stdout_lines(stdout_text)
    if not response_lines:
        return NativeExecuteResult(
            ok=False,
            table_ref=table_ref,
            results=(),
            error=f"Job {job_name} produced no JSON response on stdout",
            stdout=stdout_text,
        )

    try:
        parsed = json.loads(response_lines[-1])
    except json.JSONDecodeError as exc:
        return NativeExecuteResult(
            ok=False,
            table_ref=table_ref,
            results=(),
            error=f"Job {job_name} produced invalid JSON: {exc}",
            stdout=stdout_text,
        )

    results = tuple(_parse_operation_result(item) for item in parsed.get("results", []))
    ok = bool(parsed.get("ok", False))
    return NativeExecuteResult(
        ok=ok,
        table_ref=str(parsed.get("table_ref", table_ref)),
        results=results,
        error=None if ok else str(parsed.get("error")),
        stdout=stdout_text,
    )


def _parse_operation_result(item: Any) -> NativeOperationResult:
    if not isinstance(item, dict):
        return NativeOperationResult(
            operation="unknown",
            success=False,
            error="invalid operation result payload",
        )
    input_files = _optional_int(item.get("input_files"))
    output_files = _optional_int(item.get("output_files"))
    files_rewritten = _optional_int(item.get("files_rewritten"))
    if input_files is None:
        input_files = files_rewritten
    if output_files is None:
        output_files = files_rewritten
    return NativeOperationResult(
        operation=str(item.get("operation", "unknown")),
        success=bool(item.get("success", False)),
        files_rewritten=files_rewritten,
        input_files=input_files,
        output_files=output_files,
        bytes_removed=_optional_int(item.get("bytes_removed")),
        bytes_written=_optional_int(item.get("bytes_written")),
        snapshots_expired=_optional_int(item.get("snapshots_expired")),
        manifests_rewritten=_optional_int(item.get("manifests_rewritten")),
        error=item.get("error") if isinstance(item.get("error"), str) else None,
        duration_ms=int(item.get("duration_ms") or 0),
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fallback_subprocess(
    request: Mapping[str, Any],
    config: Mapping[str, Any],
    timeout_seconds: int | None,
) -> NativeExecuteResult:
    from cryo.engines.native.runner import execute_request

    return execute_request(
        dict(request),
        config=dict(config),
        timeout_seconds=timeout_seconds,
    )
