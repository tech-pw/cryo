"""Cryo's self-managed Spark-on-Kubernetes dispatch.

No cloud-vendor Spark service (EMR Serverless, EMR-on-EKS) required: this creates a
Kubernetes Job that runs `spark-submit --master k8s://... --deploy-mode cluster`
in-cluster. Cluster-mode spark-submit blocks until the application finishes
(``spark.kubernetes.submission.waitAppCompletion`` defaults to true), so the
dispatch Job's own exit code mirrors the real Spark application's outcome --
the same "one Job, exit code is the answer" shape as the native engine's
``cryo.engines.native.job_dispatch``.

Executor pods are cleaned up by Spark itself once the driver pod is deleted
(``spark.kubernetes.driver.pod.name`` + owner references); this dispatcher deletes
the driver pod as part of Job TTL cleanup, which cascades.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kubernetes import client

logger = logging.getLogger(__name__)

_IN_CLUSTER_API_SERVER = "https://kubernetes.default.svc:443"
_DEFAULT_IMAGE = "cryo-spark:latest"
_DEFAULT_ACTIVE_DEADLINE_SECONDS = 7200
_DEFAULT_TTL_SECONDS = 3600


class JobRunState(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


TERMINAL_STATES = frozenset({JobRunState.SUCCESS, JobRunState.FAILED})
SUCCESS_STATES = frozenset({JobRunState.SUCCESS})


@dataclass(frozen=True)
class SparkK8sConfig:
    """Config for Cryo-managed Spark-on-K8s submission (destination.config)."""

    namespace: str
    image: str = _DEFAULT_IMAGE
    image_pull_policy: str = "IfNotPresent"
    image_pull_secrets: tuple[str, ...] = ()
    service_account: str = "default"
    catalog_uri: str | None = None
    catalog_warehouse: str | None = None
    catalog_impl: str = "rest"
    s3_endpoint: str | None = None
    driver_cpu_milli: int = 1000
    driver_mem_mib: int = 2048
    executor_cpu_milli: int = 2000
    executor_mem_mib: int = 4096
    executor_instances: int = 2
    active_deadline_seconds: int = _DEFAULT_ACTIVE_DEADLINE_SECONDS
    ttl_seconds_after_finished: int = _DEFAULT_TTL_SECONDS

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> SparkK8sConfig:
        import os

        spark_cfg = config.get("spark_k8s") if isinstance(config.get("spark_k8s"), Mapping) else {}

        def _get(key: str, default: Any = None) -> Any:
            # config > CRYO_SPARK_K8S_<KEY> env (chart-injected default) > default.
            env_val = os.environ.get(f"CRYO_SPARK_K8S_{key.upper()}")
            fallback = env_val if env_val is not None else default
            return spark_cfg.get(key, config.get(f"spark_k8s_{key}", fallback))

        pull_secrets = _get("image_pull_secrets")
        if isinstance(pull_secrets, str):
            pull_secrets = tuple(s.strip() for s in pull_secrets.split(",") if s.strip())
        elif isinstance(pull_secrets, (list, tuple)):
            pull_secrets = tuple(str(s) for s in pull_secrets)
        else:
            pull_secrets = ()

        return cls(
            namespace=str(_get("namespace") or "default"),
            image=str(_get("image") or _DEFAULT_IMAGE),
            image_pull_policy=str(_get("image_pull_policy") or "IfNotPresent"),
            image_pull_secrets=pull_secrets,
            service_account=str(_get("service_account") or "default"),
            catalog_uri=_get("catalog_uri"),
            catalog_warehouse=_get("catalog_warehouse"),
            catalog_impl=str(_get("catalog_impl") or "rest"),
            s3_endpoint=_get("s3_endpoint"),
            driver_cpu_milli=int(_get("driver_cpu_milli") or 1000),
            driver_mem_mib=int(_get("driver_mem_mib") or 2048),
            executor_cpu_milli=int(_get("executor_cpu_milli") or 2000),
            executor_mem_mib=int(_get("executor_mem_mib") or 4096),
            executor_instances=int(_get("executor_instances") or 2),
            active_deadline_seconds=int(
                _get("active_deadline_seconds") or _DEFAULT_ACTIVE_DEADLINE_SECONDS
            ),
            ttl_seconds_after_finished=int(
                _get("ttl_seconds_after_finished") or _DEFAULT_TTL_SECONDS
            ),
        )


@dataclass(frozen=True)
class JobRunResult:
    job_run_id: str
    state: JobRunState
    state_details: str | None = None
    total_execution_duration_seconds: int | None = None


class SparkK8sClient:
    """Submits Spark SQL as a self-managed Kubernetes Job (spark-submit, cluster mode)."""

    def __init__(self, config: SparkK8sConfig) -> None:
        self._config = config

    def submit_spark_sql(
        self,
        sql: str,
        *,
        job_name: str = "cryo-spark",
        catalog: str = "cdp",
    ) -> str:
        cfg = self._config
        k8s_job_name = f"cryo-spark-{uuid.uuid4().hex[:10]}"
        driver_pod_name = f"{k8s_job_name}-driver"

        runner_args = [
            "--sql", sql,
            "--catalog", catalog,
            "--catalog-impl", cfg.catalog_impl,
        ]
        if cfg.catalog_uri:
            runner_args += ["--catalog-uri", cfg.catalog_uri]
        if cfg.catalog_warehouse:
            runner_args += ["--catalog-warehouse", cfg.catalog_warehouse]
        if cfg.s3_endpoint:
            runner_args += ["--s3-endpoint", cfg.s3_endpoint]

        spark_submit_cmd = [
            "/opt/spark/bin/spark-submit",
            "--master", _IN_CLUSTER_API_SERVER.replace("https://", "k8s://https://"),
            "--deploy-mode", "cluster",
            "--name", job_name,
            "--conf", f"spark.kubernetes.namespace={cfg.namespace}",
            "--conf", f"spark.kubernetes.container.image={cfg.image}",
            "--conf", f"spark.kubernetes.container.image.pullPolicy={cfg.image_pull_policy}",
            "--conf", f"spark.kubernetes.authenticate.driver.serviceAccountName={cfg.service_account}",
            "--conf", f"spark.kubernetes.driver.pod.name={driver_pod_name}",
            "--conf", f"spark.driver.cores={max(1, cfg.driver_cpu_milli // 1000)}",
            "--conf", f"spark.driver.memory={cfg.driver_mem_mib}m",
            "--conf", f"spark.executor.instances={cfg.executor_instances}",
            "--conf", f"spark.executor.cores={max(1, cfg.executor_cpu_milli // 1000)}",
            "--conf", f"spark.executor.memory={cfg.executor_mem_mib}m",
            "--conf", "spark.kubernetes.executor.deleteOnTermination=true",
            "--conf", "spark.kubernetes.submission.waitAppCompletion=true",
            "local:///opt/cryo/cryo_spark_runner.py",
            *runner_args,
        ]
        if cfg.image_pull_secrets:
            secrets_csv = ",".join(cfg.image_pull_secrets)
            spark_submit_cmd[1:1] = [
                "--conf", f"spark.kubernetes.container.image.pullSecrets={secrets_csv}",
            ]

        job_manifest: dict[str, Any] = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": k8s_job_name,
                "namespace": cfg.namespace,
                "labels": {
                    "app.kubernetes.io/name": "cryo",
                    "app.kubernetes.io/component": "spark-fallback",
                },
                "annotations": {"cryo/driver-pod-name": driver_pod_name},
            },
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": cfg.active_deadline_seconds,
                "ttlSecondsAfterFinished": cfg.ttl_seconds_after_finished,
                "template": {
                    "metadata": {
                        "labels": {
                            "app.kubernetes.io/name": "cryo",
                            "app.kubernetes.io/component": "spark-fallback",
                        },
                    },
                    "spec": {
                        "restartPolicy": "Never",
                        "serviceAccountName": cfg.service_account,
                        "imagePullSecrets": [{"name": s} for s in cfg.image_pull_secrets],
                        "containers": [
                            {
                                "name": "spark-submit",
                                "image": cfg.image,
                                "imagePullPolicy": cfg.image_pull_policy,
                                "command": spark_submit_cmd,
                                "resources": {
                                    "requests": {"cpu": "250m", "memory": "512Mi"},
                                    "limits": {"cpu": "500m", "memory": "1Gi"},
                                },
                            },
                        ],
                    },
                },
            },
        }

        batch_api = _new_k8s_batch_client()
        batch_api.create_namespaced_job(body=job_manifest, namespace=cfg.namespace)
        logger.info("Created Spark-on-K8s dispatch Job %s (driver=%s)", k8s_job_name, driver_pod_name)
        return k8s_job_name

    def get_job_run(self, job_id: str) -> JobRunResult:
        batch_api = _new_k8s_batch_client()
        job = batch_api.read_namespaced_job(name=job_id, namespace=self._config.namespace)
        status = job.status
        if status.succeeded and status.succeeded >= 1:
            return JobRunResult(job_run_id=job_id, state=JobRunState.SUCCESS)
        if status.failed and status.failed >= 1:
            detail = _read_failure_detail(job_id, self._config.namespace)
            return JobRunResult(job_run_id=job_id, state=JobRunState.FAILED, state_details=detail)
        return JobRunResult(job_run_id=job_id, state=JobRunState.RUNNING)

    def wait_for_completion(self, job_id: str) -> JobRunResult:
        deadline = time.monotonic() + self._config.active_deadline_seconds
        poll_interval = 10.0
        while time.monotonic() < deadline:
            result = self.get_job_run(job_id)
            if result.state in TERMINAL_STATES:
                return result
            time.sleep(poll_interval)
        raise TimeoutError(
            f"Spark Job {job_id} did not complete within {self._config.active_deadline_seconds}s"
        )

    def cancel_job_run(self, job_id: str) -> None:
        batch_api = _new_k8s_batch_client()
        batch_api.delete_namespaced_job(
            name=job_id,
            namespace=self._config.namespace,
            propagation_policy="Foreground",
        )


def _new_k8s_batch_client() -> client.BatchV1Api:
    from kubernetes import client

    return client.BatchV1Api()


def _new_k8s_core_client() -> client.CoreV1Api:
    from kubernetes import client

    return client.CoreV1Api()


def _read_failure_detail(job_id: str, namespace: str) -> str | None:
    """Best-effort: pull a tail of the spark-submit pod's logs for the error."""
    try:
        core_api = _new_k8s_core_client()
        pods = core_api.list_namespaced_pod(
            namespace=namespace, label_selector=f"job-name={job_id}"
        )
        for pod in pods.items:
            try:
                logs: str = core_api.read_namespaced_pod_log(
                    name=pod.metadata.name,
                    namespace=namespace,
                    limit_bytes=1024 * 8,
                )
                tail = "\n".join(logs.strip().splitlines()[-20:])
                if tail:
                    return tail
            except Exception:  # noqa: BLE001 - best-effort diagnostics only
                continue
    except Exception:  # noqa: BLE001 - best-effort diagnostics only
        pass
    return None
