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
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
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
            s3_access_key=_get("s3_access_key"),
            s3_secret_key=_get("s3_secret_key"),
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
        catalog: str,
        job_name: str = "cryo-spark",
    ) -> str:
        cfg = self._config
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
        return self._submit(runner_args, job_name=job_name)

    def submit_repair_missing_files(
        self,
        table_ref: str,
        *,
        job_name: str = "cryo-repair",
        commit: bool = False,
    ) -> str:
        """Dispatch cryo_spark_runner.py --repair-missing-files against table_ref.

        table_ref is the full catalog.namespace.table Spark identifier (e.g.
        "cdp.mview.load_test_basefact"). Its first segment is also passed as the
        runner's --catalog (the Iceberg catalog plugin registration name) -- they must
        match, or Spark can't resolve the reference at all (confirmed live, 2026-07-21:
        omitting --catalog left it registered as the runner's "glue_catalog" default,
        and querying "cdp.ns.table.files" against a catalog actually named "glue_catalog"
        threw REQUIRES_SINGLE_PART_NAMESPACE from Spark's built-in session catalog).
        Defaults to a dry run (lists what's missing without committing) -- pass
        commit=True to actually remove the confirmed-missing manifest entries.
        """
        catalog = table_ref.split(".", 1)[0]
        runner_args = ["--repair-missing-files", "--table", table_ref, "--catalog", catalog]
        if commit:
            runner_args.append("--commit")
        cfg = self._config
        if cfg.catalog_impl:
            runner_args += ["--catalog-impl", cfg.catalog_impl]
        if cfg.catalog_uri:
            runner_args += ["--catalog-uri", cfg.catalog_uri]
        if cfg.catalog_warehouse:
            runner_args += ["--catalog-warehouse", cfg.catalog_warehouse]
        if cfg.s3_endpoint:
            runner_args += ["--s3-endpoint", cfg.s3_endpoint]
        return self._submit(runner_args, job_name=job_name)

    def _submit(self, runner_args: list[str], *, job_name: str) -> str:
        cfg = self._config
        k8s_job_name = f"cryo-spark-{uuid.uuid4().hex[:10]}"
        driver_pod_name = f"{k8s_job_name}-driver"

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
            # Driver/executor pods must land on the same Karpenter on-demand capacity as
            # native-engine jobs (cryo.engines.native.job_dispatch._capacity_scheduling) --
            # that node pool's instance profile is what actually has S3 access to the
            # warehouse bucket. A generic node's instance role does not (confirmed live
            # against data-platform-cluster, 2026-07-21: AccessDeniedException on
            # s3:GetObject from a pod scheduled off that pool). Not spot: fallback SQL runs
            # aren't checkpointed, so on-demand only, matching the native engine's own rule.
            "--conf", "spark.kubernetes.node.selector.karpenter.sh/capacity-type=on-demand",
            "local:///opt/cryo/cryo_spark_runner.py",
            *runner_args,
        ]
        if cfg.image_pull_secrets:
            secrets_csv = ",".join(cfg.image_pull_secrets)
            spark_submit_cmd[1:1] = [
                "--conf", f"spark.kubernetes.container.image.pullSecrets={secrets_csv}",
            ]

        credentials_secret_name: str | None = None
        if cfg.s3_access_key and cfg.s3_secret_key:
            # secretKeyRef, not a literal --conf value: the pod spec's command array is
            # always visible via `kubectl get pod -o yaml` regardless of RBAC on Secrets,
            # so the raw credential must never appear there -- only the Secret's name does.
            # The native (Go) engine needs the same source-configured S3 credentials (the
            # node's ambient IAM role alone isn't sufficient here, confirmed live 2026-07-21).
            credentials_secret_name = f"{k8s_job_name}-aws"
            core_api = _new_k8s_core_client()
            core_api.create_namespaced_secret(
                namespace=cfg.namespace,
                body={
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": credentials_secret_name, "namespace": cfg.namespace},
                    "stringData": {
                        "access_key_id": cfg.s3_access_key,
                        "secret_access_key": cfg.s3_secret_key,
                    },
                },
            )
            # Inserted at index 1 (like the pull-secrets conf below), not appended: every
            # --conf must precede the application script path in spark-submit's argv, or
            # it gets parsed as an argument to cryo_spark_runner.py instead.
            for role in ("driver", "executor"):
                spark_submit_cmd[1:1] = [
                    "--conf",
                    f"spark.kubernetes.{role}.secretKeyRef.AWS_ACCESS_KEY_ID="
                    f"{credentials_secret_name}:access_key_id",
                    "--conf",
                    f"spark.kubernetes.{role}.secretKeyRef.AWS_SECRET_ACCESS_KEY="
                    f"{credentials_secret_name}:secret_access_key",
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
                "annotations": {
                    "cryo/driver-pod-name": driver_pod_name,
                    **(
                        {"cryo/credentials-secret-name": credentials_secret_name}
                        if credentials_secret_name
                        else {}
                    ),
                },
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
                        "annotations": {"karpenter.sh/do-not-disrupt": "true"},
                    },
                    "spec": {
                        "restartPolicy": "Never",
                        "serviceAccountName": cfg.service_account,
                        "nodeSelector": {"karpenter.sh/capacity-type": "on-demand"},
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
        """Authoritative state comes from the driver pod, not the dispatch Job.

        spark-submit's own exit code does not reliably mirror the driver's outcome --
        confirmed live against data-platform-cluster (2026-07-21): a driver pod whose SQL
        failed (container exit 1) still left the wrapping Job's spark-submit container at
        exit 0, because spark-submit's post-completion cleanup (deleting the driver's
        service/pods) threw its own RBAC exceptions in a background thread that never
        affected the main process's exit code. Trusting the wrapper Job's status alone
        silently reports SUCCESS for a run whose SQL actually failed -- always check the
        driver pod's own terminal phase instead.
        """
        driver_pod_name = f"{job_id}-driver"
        core_api = _new_k8s_core_client()
        from kubernetes.client.exceptions import ApiException

        try:
            pod = core_api.read_namespaced_pod(name=driver_pod_name, namespace=self._config.namespace)
        except ApiException as exc:
            if exc.status == 404:
                # Driver pod not created yet (still starting) or submission failed before
                # spark-submit ever created it -- the wrapper Job is the only signal we have.
                return self._get_wrapper_job_run(job_id)
            raise

        phase = pod.status.phase if pod.status else None
        if phase == "Succeeded":
            return JobRunResult(job_run_id=job_id, state=JobRunState.SUCCESS)
        if phase == "Failed":
            detail = _read_pod_log_tail(driver_pod_name, self._config.namespace)
            return JobRunResult(job_run_id=job_id, state=JobRunState.FAILED, state_details=detail)
        return JobRunResult(job_run_id=job_id, state=JobRunState.RUNNING)

    def _get_wrapper_job_run(self, job_id: str) -> JobRunResult:
        """Fallback for the window before the driver pod exists -- e.g. submission itself
        failed (bad image, RBAC on the wrapper pod) and spark-submit never got to create it.
        """
        batch_api = _new_k8s_batch_client()
        job = batch_api.read_namespaced_job(name=job_id, namespace=self._config.namespace)
        status = job.status
        if status.failed and status.failed >= 1:
            detail = _read_pod_log_tail(job_id, self._config.namespace, label_selector=f"job-name={job_id}")
            return JobRunResult(job_run_id=job_id, state=JobRunState.FAILED, state_details=detail)
        if status.succeeded and status.succeeded >= 1:
            # Wrapper finished but we never saw a driver pod -- don't trust that as success.
            return JobRunResult(
                job_run_id=job_id,
                state=JobRunState.FAILED,
                state_details="Dispatch Job finished but its Spark driver pod was never observed",
            )
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

    def cleanup_credentials_secret(self, job_id: str) -> None:
        """Delete the per-job AWS credentials Secret created by submit_spark_sql, if any.

        Best-effort: called after the run reaches a terminal state (or times out/raises)
        so credential Secrets don't accumulate. Missing/already-deleted is not an error.
        """
        try:
            batch_api = _new_k8s_batch_client()
            job = batch_api.read_namespaced_job(name=job_id, namespace=self._config.namespace)
            secret_name = (job.metadata.annotations or {}).get("cryo/credentials-secret-name")
            if not secret_name:
                return
            core_api = _new_k8s_core_client()
            core_api.delete_namespaced_secret(name=secret_name, namespace=self._config.namespace)
        except Exception:  # noqa: BLE001 - best-effort cleanup only
            logger.warning("Failed to clean up credentials Secret for job %s", job_id, exc_info=True)


_k8s_config_loaded = False


def _ensure_k8s_config() -> None:
    """Load in-cluster (or local kubeconfig) auth once per process.

    Without this, the kubernetes client defaults to localhost:80 and every call fails
    with ConnectionRefusedError -- confirmed live (2026-07-21): this worked from
    Governor/Worker only because cryo.engines.native.job_dispatch happens to call
    load_incluster_config() as a side effect of its own (unrelated) capacity check
    before any Spark dispatch code runs in that process. A process that never touches
    native job dispatch (the API pod, calling submit_repair_missing_files directly) hit
    this cold. Each client factory must ensure its own prerequisite, not depend on a
    side effect from a different module happening to run first.
    """
    global _k8s_config_loaded
    if _k8s_config_loaded:
        return
    from kubernetes import config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except Exception:
        k8s_config.load_kube_config()
    _k8s_config_loaded = True


def _new_k8s_batch_client() -> client.BatchV1Api:
    from kubernetes import client

    _ensure_k8s_config()
    return client.BatchV1Api()


def _new_k8s_core_client() -> client.CoreV1Api:
    from kubernetes import client

    _ensure_k8s_config()
    return client.CoreV1Api()


def _read_pod_log_tail(
    pod_name: str, namespace: str, *, label_selector: str | None = None
) -> str | None:
    """Best-effort: pull a tail of a pod's logs for the error.

    Direct name lookup by default (used for the driver pod, whose name is known);
    falls back to a label-selector list when the caller only knows the owning Job
    (the wrapper-pod fallback path, where the pod's generated suffix isn't known).
    """
    try:
        core_api = _new_k8s_core_client()
        if label_selector:
            pods = core_api.list_namespaced_pod(
                namespace=namespace, label_selector=label_selector
            ).items
        else:
            pods = [core_api.read_namespaced_pod(name=pod_name, namespace=namespace)]
        for pod in pods:
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
