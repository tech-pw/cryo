"""Kubernetes Job-based dispatch for cryo-engine binaries.

Replaces the subprocess runner for native engine execution when running inside a
Kubernetes cluster. Each maintenance operation (or chunk) is dispatched as a
separate Kubernetes Job, with resource requests sized from the Planner's
`total_size_gb` / `chunk_size_gb` / file-count data.

Architecture:
- Worker creates a Job per chunk using the kubernetes Python client
- Payload is passed as a base64-encoded env var to avoid ConfigMap overhead
- Job stdout is captured via pod logs on completion
- Idempotent: Kubernetes Job `.spec.ttlSecondsAfterFinished` cleans up completed pods
- Graceful fallback: if kubernetes client is unavailable or not in-cluster, falls
  back to the subprocess runner
- Spot capacity is gated (docs/native-engine-scoping-research.md §6.5): large or
  uncheckpointed chunks force on-demand even when spot is requested
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kubernetes import client

from cryo.engines.native.runner import (
    NativeExecuteResult,
    NativeOperationResult,
    split_stdout_lines,
)

logger = logging.getLogger(__name__)

_CPU_BASE_MILLI = 250
_CPU_PER_10GB = 100
_MEM_BASE_MIB = 512
_MEM_PER_10GB = 128
_MAX_CPU_MILLI = 4000
_MAX_MEM_MIB = 16384
_ACTIVE_DEADLINE_SECONDS = 7200
_DEFAULT_GB_PER_HOUR = 40.0


def compute_job_resources(chunk_size_gb: float) -> tuple[int, int]:
    cpu_milli = min(
        _MAX_CPU_MILLI,
        _CPU_BASE_MILLI + int(chunk_size_gb / 10 * _CPU_PER_10GB),
    )
    mem_mib = min(
        _MAX_MEM_MIB,
        _MEM_BASE_MIB + int(chunk_size_gb / 10 * _MEM_PER_10GB),
    )
    return cpu_milli, mem_mib


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
) -> tuple[dict[str, str], list[dict[str, str]], dict[str, str]]:
    """Return (node_selector extras, tolerations, annotations) for capacity type."""
    annotations = {
        "cryo.io/capacity-type": "spot" if use_spot else "on-demand",
    }
    if not use_spot:
        annotations["karpenter.sh/do-not-disrupt"] = "true"
        return (
            {"karpenter.sh/capacity-type": "on-demand"},
            [],
            annotations,
        )
    return (
        {"karpenter.sh/capacity-type": "spot"},
        [
            {
                "key": "karpenter.sh/capacity-type",
                "operator": "Equal",
                "value": "spot",
                "effect": "NoSchedule",
            }
        ],
        annotations,
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


@dataclass(frozen=True)
class JobDispatchResult:
    ok: bool
    stdout: str | None
    stderr: str | None
    job_name: str | None
    error: str | None


def execute_request_via_job(
    request: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    job_name: str | None = None,
    chunk_size_gb: float = 100.0,
    timeout_seconds: int | None = None,
) -> NativeExecuteResult:
    """Dispatch cryo-engine execution as a Kubernetes Job and return results.

    Falls back to subprocess runner when kubernetes client is unavailable or not
    running in-cluster.
    """
    k8s_client, _ = _safe_load_k8s()
    if k8s_client is None:
        logger.warning("kubernetes client not available, falling back to subprocess")
        return _fallback_subprocess(request, config, timeout_seconds)

    if not _in_cluster_configured():
        logger.warning("not running in-cluster, falling back to subprocess")
        return _fallback_subprocess(request, config, timeout_seconds)

    table_ref = str(request.get("table_ref", "unknown"))
    if job_name is None:
        import uuid

        safe_uuid = str(uuid.uuid4())[:8]
        job_name = f"cryo-native-{safe_uuid}"

    payload_json = json.dumps(dict(request))
    payload_b64 = base64.b64encode(payload_json.encode()).decode()

    cpu_milli, mem_mib = compute_job_resources(chunk_size_gb)
    timeout_seconds = timeout_seconds or _ACTIVE_DEADLINE_SECONDS

    image = str(config.get("k8s_image", "cryo:latest"))
    binary = str(config.get("binary_path", "/usr/local/bin/cryo-engine-go"))
    service_account = str(
        config.get("k8s_service_account")
        or os.environ.get("CRYO_NATIVE_K8S_SERVICE_ACCOUNT")
        or "default"
    )
    ttl_seconds = int(config.get("k8s_ttl_seconds_after_finished", 300))
    image_pull_policy = str(config.get("k8s_image_pull_policy", "IfNotPresent"))
    namespace = str(
        config.get("k8s_namespace") or os.environ.get("CRYO_K8S_NAMESPACE") or "default"
    )
    destination_id = str(config.get("destination_id") or config.get("id") or "")

    use_spot = spot_allowed_for_job(chunk_size_gb=chunk_size_gb, config=config)
    node_extras, tolerations, capacity_annotations = _capacity_scheduling(use_spot=use_spot)
    if not use_spot and _bool_cfg(
        config, "k8s_spot_allow", "CRYO_NATIVE_SPOT_ALLOW", default=False
    ):
        logger.info(
            "Spot requested but gated off for chunk_size_gb=%.1f; using on-demand",
            chunk_size_gb,
        )

    node_selector: dict[str, str] = {"kubernetes.io/arch": "amd64"}
    node_selector.update(node_extras)
    safe_table_ref = table_ref[:63]

    job_manifest: dict[str, Any] = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "cryo",
                "app.kubernetes.io/component": "native-engine",
                "cryo/table_ref": safe_table_ref,
                "cryo/destination": destination_id or "default",
            },
            "annotations": {
                "cryo/cpu": f"{cpu_milli}m",
                "cryo/memory": f"{mem_mib}Mi",
                **capacity_annotations,
            },
        },
        "spec": {
            "ttlSecondsAfterFinished": ttl_seconds,
            "activeDeadlineSeconds": timeout_seconds,
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/name": "cryo",
                        "app.kubernetes.io/component": "native-engine",
                        "cryo/table_ref": safe_table_ref,
                        "cryo/destination": destination_id or "default",
                    },
                    "annotations": dict(capacity_annotations),
                },
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": service_account,
                    "nodeSelector": node_selector,
                    "tolerations": tolerations,
                    "containers": [
                        {
                            "name": "cryo-engine",
                            "image": image,
                            "imagePullPolicy": image_pull_policy,
                            "command": [binary, "execute"],
                            "env": [
                                {
                                    "name": "CRYO_PAYLOAD_B64",
                                    "value": payload_b64,
                                },
                            ],
                            "resources": {
                                "requests": {
                                    "cpu": f"{cpu_milli}m",
                                    "memory": f"{mem_mib}Mi",
                                },
                                "limits": {
                                    "cpu": f"{min(cpu_milli * 2, _MAX_CPU_MILLI)}m",
                                    "memory": f"{min(mem_mib * 2, _MAX_MEM_MIB)}Mi",
                                },
                            },
                        },
                    ],
                },
            },
        },
    }

    batch_api = _new_k8s_batch_client()

    try:
        batch_api.create_namespaced_job(body=job_manifest, namespace=namespace)
        logger.info(
            "Created Kubernetes Job %s for table %s (spot=%s cpu=%sm mem=%sMi)",
            job_name,
            table_ref,
            use_spot,
            cpu_milli,
            mem_mib,
        )
    except Exception as exc:
        logger.error("Failed to create Job %s: %s", job_name, exc)
        return NativeExecuteResult(
            ok=False,
            table_ref=table_ref,
            results=(),
            error=f"Failed to create Kubernetes Job: {exc}",
        )

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
                limit_bytes=1024 * 512,
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
