"""K8s Job dispatch for the analysis runner (recovery plan §2, 2026-09-20).

Deliberately much simpler than cryo.engines.native.job_dispatch.create_native_job: that
function's payload-via-mounted-Secret plumbing exists because native-engine batch payloads can
carry hundreds of thousands of file paths and blow past exec's MAX_ARG_STRLEN. The analysis
runner's own "payload" is a single CLI positional arg (a task_type string, e.g.
"benefit_score_sweep") -- there is nothing here that needs a Secret, so this dispatcher builds a
minimal, self-contained Job manifest directly rather than reusing that heavier machinery.

Resource sizing is a fixed, conservative default (see _DEFAULT_CPU_MILLI/_DEFAULT_MEM_MIB)
rather than a per-task estimate -- R1's per-runner profiling has not yet measured this runner's
real peak RSS against the live 13k-table fleet, so this value is a deliberately generous
placeholder pending that measurement (recovery plan §7 ordering note: "R1 instrumentation can
land immediately," implying its numbers should feed back into sizing here once available).
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

from cryo.engines.native.job_state import current_k8s_namespace

logger = logging.getLogger(__name__)

# Conservative placeholder pending real R1 profiling of this runner against the live fleet.
_DEFAULT_CPU_MILLI = 500
_DEFAULT_MEM_MIB = 2048
_DEFAULT_ACTIVE_DEADLINE_SECONDS = 900
_DEFAULT_TTL_SECONDS_AFTER_FINISHED = 3600


@dataclass(frozen=True)
class AnalysisJobResult:
    ok: bool
    job_name: str | None
    error: str | None = None


def _safe_load_k8s() -> tuple[Any, Any] | tuple[None, None]:
    try:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config

        return k8s_client, k8s_config
    except ImportError:
        return None, None


def _in_cluster_configured(k8s_config: Any) -> bool:
    try:
        k8s_config.load_incluster_config()
        return True
    except Exception:
        return False


def k8s_dispatch_available() -> bool:
    k8s_client, k8s_config = _safe_load_k8s()
    return k8s_client is not None and _in_cluster_configured(k8s_config)


def dispatch_analysis_job(
    task_type: str,
    *,
    image: str | None = None,
    service_account: str | None = None,
    namespace: str | None = None,
    image_pull_policy: str | None = None,
) -> AnalysisJobResult:
    """Create a one-shot K8s Job running `cryo analysis <task_type>`.

    Returns ok=False (never raises) when the k8s client is unavailable or not running in-cluster
    -- the caller (the scheduler coordinator) is expected to fall back to logging and retrying on
    its next tick rather than crash the scheduler process over a missing Job-dispatch capability,
    same fallback contract as cryo.engines.native.job_dispatch.create_native_job.
    """
    k8s_client, k8s_config = _safe_load_k8s()
    if k8s_client is None:
        return AnalysisJobResult(ok=False, job_name=None, error="kubernetes client not available")
    if not _in_cluster_configured(k8s_config):
        return AnalysisJobResult(ok=False, job_name=None, error="not running in-cluster")

    job_name = f"cryo-analysis-{task_type.replace('_', '-')}-{uuid.uuid4().hex[:8]}"[:63]
    resolved_namespace = namespace or current_k8s_namespace()
    resolved_image = (
        image
        or os.environ.get("CRYO_ANALYSIS_IMAGE")
        or os.environ.get("CRYO_NATIVE_IMAGE")
        or "cryo:latest"
    )
    resolved_service_account = (
        service_account or os.environ.get("CRYO_ANALYSIS_SERVICE_ACCOUNT") or "default"
    )
    resolved_pull_policy = image_pull_policy or os.environ.get(
        "CRYO_ANALYSIS_IMAGE_PULL_POLICY", "IfNotPresent"
    )

    labels = {
        "app.kubernetes.io/name": "cryo",
        "app.kubernetes.io/component": "analysis-runner",
        "app": "cryo-analysis",
        "cryo/task-type": task_type[:63],
    }

    job_manifest: dict[str, Any] = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name, "namespace": resolved_namespace, "labels": labels},
        "spec": {
            "ttlSecondsAfterFinished": _DEFAULT_TTL_SECONDS_AFTER_FINISHED,
            "activeDeadlineSeconds": _DEFAULT_ACTIVE_DEADLINE_SECONDS,
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": resolved_service_account,
                    "containers": [
                        {
                            "name": "cryo-analysis",
                            "image": resolved_image,
                            "imagePullPolicy": resolved_pull_policy,
                            "command": ["python", "-m", "cryo.cli", "analysis", task_type],
                            "resources": {
                                "requests": {
                                    "cpu": f"{_DEFAULT_CPU_MILLI}m",
                                    "memory": f"{_DEFAULT_MEM_MIB}Mi",
                                },
                                "limits": {
                                    "cpu": f"{_DEFAULT_CPU_MILLI * 2}m",
                                    "memory": f"{_DEFAULT_MEM_MIB * 2}Mi",
                                },
                            },
                        }
                    ],
                },
            },
        },
    }

    batch_api = k8s_client.BatchV1Api()
    try:
        batch_api.create_namespaced_job(body=job_manifest, namespace=resolved_namespace)
        logger.info("Created analysis Job %s for task_type=%s", job_name, task_type)
        return AnalysisJobResult(ok=True, job_name=job_name)
    except Exception as exc:
        logger.error("Failed to create analysis Job %s: %s", job_name, exc)
        return AnalysisJobResult(ok=False, job_name=job_name, error=str(exc))
