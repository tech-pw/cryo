"""Cryo's self-managed Spark-on-Kubernetes dispatch.

No cloud-vendor Spark service (EMR Serverless, EMR-on-EKS) required: this creates a
Kubernetes Job that runs `spark-submit --master k8s://... --deploy-mode cluster`
in-cluster. Cluster-mode spark-submit blocks until the application finishes
(``spark.kubernetes.submission.waitAppCompletion`` defaults to true), so the
dispatch Job's own exit code mirrors the real Spark application's outcome --
the same "one Job, exit code is the answer" shape as the native engine's
``cryo.engines.native.job_dispatch``.

Spark deletes executor pods on termination
(``spark.kubernetes.executor.deleteOnTermination``), but **keeps the completed
driver pod** by design (for logs). That driver is created by spark-submit and is
**not** owned by our wrapper Job, so Job ``ttlSecondsAfterFinished`` does not
remove it. Callers must invoke :meth:`SparkK8sClient.cleanup_after_run` once the
run is terminal (see ``SparkK8sEngine.execute``) to delete the driver pod and the
per-job AWS credentials Secret.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kubernetes import client

from cryo.engines.base import deepest_caused_by
from cryo.engines.k8s_cost_labels import cost_attribution_labels
from cryo.engines.k8s_security import container_security_context, pod_security_context
from cryo.engines.native.job_state import current_k8s_namespace
from cryo.engines.spark.sizing import (
    DRIVER_MEMORY_OVERHEAD_MIB,
    LOCAL_MODE_OPS,
    ExecutorProfile,
    cap_deadline_seconds_for_table,
    cap_dispatch_profile,
    driver_max_result_size_mib,
    executor_memory_overhead_mib,
    fold_into_local_driver,
    lightweight_driver_heap_mib,
    local_master,
)

logger = logging.getLogger(__name__)

_IN_CLUSTER_API_SERVER = "https://kubernetes.default.svc:443"
_DEFAULT_IMAGE = "cryo-spark:latest"
# A maintenance driver mostly waits on executors and S3; observed use is ~1% of a core.
# Reserve a quarter of its limit and keep the limit, so bursts (planning, commit) still get
# the full core. 1.0 restores request == limit.
_DEFAULT_DRIVER_CPU_REQUEST_FRACTION = 0.25
_MIN_DRIVER_CPU_REQUEST_MILLI = 250
# The wrapper only runs spark-submit and polls the driver until it exits.
_WRAPPER_REQUESTS = {"cpu": "50m", "memory": "256Mi"}
_DEFAULT_ACTIVE_DEADLINE_SECONDS = 7200
_DEFAULT_TTL_SECONDS = 3600
_DESTINATION_LABEL = "cryo/destination"


class JobRunState(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    # Distinct from FAILED: FAILED means the driver/Job told us it failed. UNKNOWN means
    # both the driver pod and the wrapper Job are gone (404) -- normal once
    # ttlSecondsAfterFinished passes, but if a caller is asking this late, the real outcome
    # is genuinely unrecoverable, not "known to have failed". Confirmed live, 2026-08-12:
    # a scheduler RBAC gap (fixed separately) left 8,401 async fallback dispatches
    # unresolved from 2026-08-11's incident window; by the time the sweep could reach
    # them, ~8,348 had already TTL-expired -- this state lets that be recorded honestly
    # instead of either a bare lookup error (looks like a bug) or a false FAILED (implies
    # a known outcome that was never actually observed). Also returned when the wrapper Job
    # succeeded, the driver pod is gone, and the wrapper log holds no final driver status.
    UNKNOWN = "UNKNOWN"


TERMINAL_STATES = frozenset({JobRunState.SUCCESS, JobRunState.FAILED, JobRunState.UNKNOWN})
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
    node_selector: dict[str, str] | None = None
    active_deadline_seconds: int = _DEFAULT_ACTIVE_DEADLINE_SECONDS
    ttl_seconds_after_finished: int = _DEFAULT_TTL_SECONDS
    local_mode: bool = True
    tolerations: tuple[dict[str, str], ...] = ()
    pack_pods: bool = False
    driver_cpu_request_fraction: float = _DEFAULT_DRIVER_CPU_REQUEST_FRACTION

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> SparkK8sConfig:
        import os

        raw_spark_cfg = config.get("spark_k8s")
        spark_cfg: Mapping[str, Any] = raw_spark_cfg if isinstance(raw_spark_cfg, Mapping) else {}

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

        # node_selector: allow CRYO_SPARK_K8S_NODE_SELECTOR env as comma-separated key=val pairs.
        # A caller-supplied selector used to replace the default entirely. The chart default is
        # only `karpenter.sh/capacity-type=on-demand`, so on a mixed amd64/arm64 cluster the
        # wrapper Job landed on Graviton and died before Spark started: "exec
        # /opt/spark/bin/spark-submit: exec format error" (data-platform, 2026-10-19,
        # cryo-spark-f7c80a2012 on an r8g node; same failure 2026-10-05, job e3aa32fd).
        # ghcr.io/tech-pw/cryo-spark is amd64-only until the release workflow publishes a
        # manifest list. Merge the arch pin back unless the caller set the key itself —
        # an explicit arm64 is the escape hatch for an install that built its own image.
        # Do not default the Karpenter capacity label here: a cluster without that label
        # could not schedule. Installs that want it set spark.k8s.nodeSelector.
        _node_sel_raw = _get("node_selector")
        if isinstance(_node_sel_raw, str):
            pairs = [p.strip() for p in _node_sel_raw.split(",") if p.strip()]
            node_selector = {}
            for p in pairs:
                if "=" in p:
                    k, v = p.split("=", 1)
                    node_selector[k.strip()] = v.strip()
        elif isinstance(_node_sel_raw, Mapping):
            node_selector = dict(_node_sel_raw)
        else:
            node_selector = {}
        node_selector.setdefault("kubernetes.io/arch", "amd64")

        return cls(
            # Confirmed live 2026-10-01: a destination.config with no explicit "namespace"
            # key previously fell back to the literal string "default" -- the Governor's RBAC
            # Role only grants batch/jobs and secrets verbs in its own namespace, so dispatch
            # 403'd trying to create the driver's S3-credentials Secret in Kubernetes'
            # "default" namespace. current_k8s_namespace() (same helper the native engine
            # uses) falls back to wherever this process is actually running instead.
            namespace=str(_get("namespace") or current_k8s_namespace()),
            image=str(_get("image") or _DEFAULT_IMAGE),
            image_pull_policy=str(_get("image_pull_policy") or "IfNotPresent"),
            image_pull_secrets=pull_secrets,
            service_account=str(_get("service_account") or "default"),
            # A destination row stores these at the top level of config. The nested
            # spark_k8s block and the env var still win, but a value that exists only at
            # the top level must not be dropped (issue #149, 2026-10-21).
            catalog_uri=_get("catalog_uri") or config.get("catalog_uri"),
            catalog_warehouse=_get("catalog_warehouse") or config.get("catalog_warehouse"),
            catalog_impl=str(_get("catalog_impl") or config.get("catalog_impl") or "rest"),
            s3_endpoint=_get("s3_endpoint"),
            s3_access_key=_get("s3_access_key"),
            s3_secret_key=_get("s3_secret_key"),
            driver_cpu_milli=int(_get("driver_cpu_milli") or 1000),
            driver_mem_mib=int(_get("driver_mem_mib") or 2048),
            executor_cpu_milli=int(_get("executor_cpu_milli") or 2000),
            executor_mem_mib=int(_get("executor_mem_mib") or 4096),
            executor_instances=int(_get("executor_instances") or 2),
            node_selector=node_selector,
            active_deadline_seconds=int(
                _get("active_deadline_seconds") or _DEFAULT_ACTIVE_DEADLINE_SECONDS
            ),
            ttl_seconds_after_finished=int(
                _get("ttl_seconds_after_finished") or _DEFAULT_TTL_SECONDS
            ),
            local_mode=str(_get("local_mode", "true")).strip().lower()
            not in ("0", "false", "no", "off"),
            tolerations=_parse_tolerations(_get("tolerations")),
            pack_pods=str(_get("pack_pods", "false")).strip().lower() in ("1", "true", "yes", "on"),
            driver_cpu_request_fraction=float(
                _get("driver_cpu_request_fraction") or _DEFAULT_DRIVER_CPU_REQUEST_FRACTION
            ),
        )


def _parse_tolerations(raw: Any) -> tuple[dict[str, str], ...]:
    """``key=value:Effect`` or ``key:Effect``, comma-separated, or a list of toleration dicts."""
    if isinstance(raw, (list, tuple)):
        return tuple({str(k): str(v) for k, v in t.items()} for t in raw if isinstance(t, Mapping))
    if not isinstance(raw, str):
        return ()
    out: list[dict[str, str]] = []
    for item in (p.strip() for p in raw.split(",")):
        if not item:
            continue
        spec, _, effect = item.rpartition(":")
        if not spec:
            spec, effect = item, ""
        key, sep, value = spec.partition("=")
        tol = {"key": key.strip(), "operator": "Equal" if sep else "Exists"}
        if sep:
            tol["value"] = value.strip()
        if effect.strip():
            tol["effect"] = effect.strip()
        out.append(tol)
    return tuple(out)


_POD_TEMPLATE_DIR = "/tmp/cryo-pod-templates"
# Spark labels every driver and executor with its application id. matchLabelKeys narrows a
# term to pods with the same value, so the first term pulls a job's executors next to its
# driver; the second fills nodes that already run Spark before Karpenter adds one.
_PACK_AFFINITY: dict[str, Any] = {
    "podAffinity": {
        "preferredDuringSchedulingIgnoredDuringExecution": [
            {
                "weight": 100,
                "podAffinityTerm": {
                    "labelSelector": {
                        "matchExpressions": [{"key": "spark-app-selector", "operator": "Exists"}]
                    },
                    "matchLabelKeys": ["spark-app-selector"],
                    "topologyKey": "kubernetes.io/hostname",
                },
            },
            {
                "weight": 50,
                "podAffinityTerm": {
                    "labelSelector": {
                        "matchExpressions": [{"key": "spark-role", "operator": "Exists"}]
                    },
                    "topologyKey": "kubernetes.io/hostname",
                },
            },
        ]
    }
}


def _attach_pod_templates(
    pod_spec: dict[str, Any], templates: Mapping[str, str], cfg: SparkK8sConfig
) -> None:
    """spark-submit reads the template files locally, so write them into the wrapper first."""
    container = pod_spec["containers"][0]
    writes = " && ".join(
        f'printf %s "$CRYO_{role.upper()}_POD_TEMPLATE" > {_POD_TEMPLATE_DIR}/{role}.json'
        for role in templates
    )
    container["command"] = ["/bin/sh", "-c", f'{writes} && exec "$@"', "sh", *container["command"]]
    container["env"] = [
        *container.get("env", []),
        *(
            {"name": f"CRYO_{role.upper()}_POD_TEMPLATE", "value": template}
            for role, template in templates.items()
        ),
    ]
    container["volumeMounts"] = [{"name": "pod-templates", "mountPath": _POD_TEMPLATE_DIR}]
    pod_spec["volumes"] = [{"name": "pod-templates", "emptyDir": {}}]
    if cfg.tolerations:
        pod_spec["tolerations"] = [dict(t) for t in cfg.tolerations]


def _pod_templates(cfg: SparkK8sConfig) -> dict[str, str] | None:
    """Driver and executor pod templates as JSON, or None when nothing needs one."""
    if not cfg.tolerations and not cfg.pack_pods:
        return None
    spec: dict[str, Any] = {}
    if cfg.tolerations:
        spec["tolerations"] = [dict(t) for t in cfg.tolerations]
    if cfg.pack_pods:
        spec["affinity"] = _PACK_AFFINITY
    pod = json.dumps({"apiVersion": "v1", "kind": "Pod", "spec": spec}, sort_keys=True)
    return {"driver": pod, "executor": pod}


@dataclass(frozen=True)
class JobRunResult:
    job_run_id: str
    state: JobRunState
    state_details: str | None = None
    total_execution_duration_seconds: int | None = None
    bytes_removed: int | None = None
    # When the driver pod started (RUNNING only). Orphan recovery measures the Job deadline
    # from it, since a recovering worker has no wait_for_completion clock of its own (#258).
    started_at: datetime | None = None
    # The procedure's own counters from the runner's last [cryo-result] line (issue #155).
    metrics: dict[str, Any] | None = None


class SparkK8sClient:
    """Submits Spark SQL as a self-managed Kubernetes Job (spark-submit, cluster mode)."""

    def __init__(self, config: SparkK8sConfig) -> None:
        self._config = config
        self._batch_api: client.BatchV1Api | None = None
        self._core_api: client.CoreV1Api | None = None

    def _batch_client(self) -> client.BatchV1Api:
        """One BatchV1Api per SparkK8sClient, not per call.

        Every kubernetes-client ApiClient owns its own urllib3.PoolManager (SSL contexts,
        connection pools, certifi bundle) -- real, non-trivial memory that is never freed
        unless .close() is called. Before this fix, every method below (_submit,
        get_job_run, wait_for_completion's per-poll get_job_run, cleanup_after_run, ...)
        called _new_k8s_batch_client()/_new_k8s_core_client() fresh, so a single
        wait_for_completion() poll loop (10s interval, up to active_deadline_seconds ~2h)
        leaked one PoolManager per poll -- confirmed live, 2026-08-13: cryo-worker pods
        OOMKilled repeatedly (8 of 10 running pods, exitCode 137) despite idling at
        ~150-190Mi against a 4Gi limit, i.e. a spike from bursty accumulation, not steady
        growth. One cached client per SparkK8sClient instance (which SparkK8sEngine
        already caches per engine instance via its own k8s_client property) closes this --
        matches the pattern that property already uses one level up.
        """
        if self._batch_api is None:
            self._batch_api = _new_k8s_batch_client()
        return self._batch_api

    def _core_client(self) -> client.CoreV1Api:
        if self._core_api is None:
            self._core_api = _new_k8s_core_client()
        return self._core_api

    def submit_spark_sql(
        self,
        sql: str,
        *,
        catalog: str,
        job_name: str = "cryo-spark",
        destination_id: str | None = None,
        operation: str | None = None,
        total_size_gb: float | None = None,
        total_files: int | None = None,
        oom_steps: int = 0,
        executor_oom_steps: int = 0,
    ) -> str:
        cfg = self._config
        runner_args = [
            "--sql",
            sql,
            "--catalog",
            catalog,
            "--catalog-impl",
            cfg.catalog_impl,
        ]
        if cfg.catalog_uri:
            runner_args += ["--catalog-uri", cfg.catalog_uri]
        if cfg.catalog_warehouse:
            runner_args += ["--catalog-warehouse", cfg.catalog_warehouse]
        if cfg.s3_endpoint:
            runner_args += ["--s3-endpoint", cfg.s3_endpoint]
        return self._submit(
            runner_args,
            job_name=job_name,
            destination_id=destination_id,
            operation=operation,
            extra_confs=_merge_confs(
                catalog_io_confs(catalog, cfg.catalog_warehouse),
                driver_pressure_confs(operation, total_files=total_files, oom_steps=oom_steps),
            ),
            total_size_gb=total_size_gb,
            total_files=total_files,
            oom_steps=oom_steps,
            executor_oom_steps=executor_oom_steps,
        )

    def submit_repair_missing_files(
        self,
        table_ref: str,
        *,
        job_name: str = "cryo-repair",
        commit: bool = False,
        destination_id: str | None = None,
        operation: str = "repair_missing_files",
    ) -> str:
        """Dispatch cryo_spark_runner.py --repair-missing-files against table_ref.

        table_ref is the full catalog.namespace.table Spark identifier (e.g.
        "rest_catalog.db.sample_table"). Its first segment is also passed as the
        runner's --catalog (the Iceberg catalog plugin registration name) -- they must
        match, or Spark can't resolve the reference at all (confirmed live, 2026-07-21:
        omitting --catalog left it registered as the runner's "glue_catalog" default,
        and querying "rest_catalog.ns.table.files" against a catalog actually named "glue_catalog"
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
        return self._submit(
            runner_args,
            job_name=job_name,
            destination_id=destination_id,
            operation=operation,
        )

    def _submit(
        self,
        runner_args: list[str],
        *,
        job_name: str,
        destination_id: str | None = None,
        operation: str | None = None,
        extra_confs: Mapping[str, str] | None = None,
        total_size_gb: float | None = None,
        total_files: int | None = None,
        oom_steps: int = 0,
        executor_oom_steps: int = 0,
    ) -> str:
        cfg = _capped_submit_config(
            self._config,
            operation=operation,
            total_size_gb=total_size_gb,
            total_files=total_files,
            oom_steps=oom_steps,
            executor_oom_steps=executor_oom_steps,
        )
        # A 1 GiB retry still sits under the 25% floor, so the step count doubles it (#431).
        executor_overhead = executor_memory_overhead_mib(
            cfg.executor_mem_mib, oom_steps=executor_oom_steps
        )
        if cfg.local_mode and operation in LOCAL_MODE_OPS:
            cfg = _fold_local_driver(cfg)
            runner_args = [*runner_args, "--local-master", local_master(cfg.driver_cpu_milli)]
        k8s_job_name = f"cryo-spark-{uuid.uuid4().hex[:10]}"
        driver_pod_name = f"{k8s_job_name}-driver"

        spark_submit_cmd = [
            "/opt/spark/bin/spark-submit",
            "--master",
            _IN_CLUSTER_API_SERVER.replace("https://", "k8s://https://"),
            "--deploy-mode",
            "cluster",
            "--name",
            job_name,
            "--conf",
            f"spark.kubernetes.namespace={cfg.namespace}",
            "--conf",
            f"spark.kubernetes.container.image={cfg.image}",
            "--conf",
            f"spark.kubernetes.container.image.pullPolicy={cfg.image_pull_policy}",
            "--conf",
            f"spark.kubernetes.authenticate.driver.serviceAccountName={cfg.service_account}",
            "--conf",
            f"spark.kubernetes.driver.pod.name={driver_pod_name}",
            "--conf",
            f"spark.driver.cores={max(1, cfg.driver_cpu_milli // 1000)}",
            "--conf",
            f"spark.driver.memory={cfg.driver_mem_mib}m",
            "--conf",
            f"spark.executor.instances={cfg.executor_instances}",
            "--conf",
            f"spark.executor.cores={max(1, cfg.executor_cpu_milli // 1000)}",
            "--conf",
            f"spark.executor.memory={cfg.executor_mem_mib}m",
            "--conf",
            "spark.kubernetes.executor.deleteOnTermination=true",
            "--conf",
            "spark.kubernetes.submission.waitAppCompletion=true",
            # Explicit Kubernetes resource requests/limits so Karpenter can pack nodes
            # accurately instead of guessing from Spark's integer core counts.
            "--conf",
            f"spark.kubernetes.driver.request.cores={_driver_request_milli(cfg) / 1000.0:.3f}",
            "--conf",
            f"spark.kubernetes.driver.limit.cores={cfg.driver_cpu_milli / 1000.0:.3f}",
            "--conf",
            f"spark.kubernetes.executor.request.cores={cfg.executor_cpu_milli / 1000.0:.3f}",
            "--conf",
            f"spark.kubernetes.executor.limit.cores={cfg.executor_cpu_milli / 1000.0:.3f}",
            # Spark 4.1.2 BasicDriverFeatureStep / BasicExecutorFeatureStep set the
            # container request and limit to heap + memoryOverhead. Neither reads
            # spark.kubernetes.*.limit.memory or *.request.memory. The previous driver
            # limit conf, set equal to the heap, was unused by Spark and did not reserve
            # the 384MiB overhead. cryo-spark-069a3d881f-driver was OOMKilled (exit 137,
            # 2026-10-19) with no JVM OutOfMemoryError. Pin the overhead Spark does add.
            # Do not set the undocumented memory request/limit confs.
            "--conf",
            f"spark.driver.memoryOverhead={DRIVER_MEMORY_OVERHEAD_MIB}m",
            "--conf",
            f"spark.executor.memoryOverhead={executor_overhead}m",
            # Wrapper Job already has do-not-disrupt; driver/executor pods are created by
            # spark-submit and need the same annotation or Karpenter consolidation can
            # SIGTERM them mid-rewrite (example-cluster 2026-08-12: run 3af23a9f,
            # Evicted Underutilized → exit 143 → misleading "driver never observed").
            "--conf",
            "spark.kubernetes.driver.annotation.karpenter.sh/do-not-disrupt=true",
            "--conf",
            "spark.kubernetes.executor.annotation.karpenter.sh/do-not-disrupt=true",
            "local:///opt/cryo/cryo_spark_runner.py",
            *runner_args,
        ]

        cost_labels = cost_attribution_labels(
            workload_name="cryo-spark",
            engine="spark",
            operation=operation or job_name,
        )
        spark_job_labels = {
            "app.kubernetes.io/name": "cryo",
            "app.kubernetes.io/component": "spark-fallback",
            _DESTINATION_LABEL: (destination_id or "default")[:63],
            **cost_labels,
        }
        # Driver/executor pods are created by spark-submit, not the wrapper Job. CUR
        # attributes reserved CPU/memory on those pods. Hyphenated keys only: Spark
        # config treats '.' as nesting, so app.kubernetes.io/* cannot go on this conf.
        label_confs: list[str] = []
        for key, value in cost_labels.items():
            label_confs.extend(
                [
                    "--conf",
                    f"spark.kubernetes.driver.label.{key}={value}",
                    "--conf",
                    f"spark.kubernetes.executor.label.{key}={value}",
                ]
            )
        spark_submit_cmd[1:1] = label_confs
        for conf_key, conf_value in (extra_confs or {}).items():
            spark_submit_cmd[1:1] = ["--conf", f"{conf_key}={conf_value}"]

        # Driver/executor pods must land on the same Karpenter on-demand capacity as
        # native-engine jobs (cryo.engines.native.job_dispatch._capacity_scheduling) --
        # that node pool's instance profile is what actually has S3 access to the
        # warehouse bucket. A generic node's instance role does not (confirmed live
        # against example-cluster, 2026-07-21: AccessDeniedException on
        # s3:GetObject from a pod scheduled off that pool). Not spot: fallback SQL runs
        # aren't checkpointed, so on-demand only, matching the native engine's own rule --
        # enforced via cfg.node_selector, which defaults to on-demand (see from_config
        # above) but stays overridable per-destination rather than hardcoded here.
        #
        # Insert node selector spark.conf entries so they appear before the app path
        # (insert at index 1 to keep all --conf values before the application script)
        if cfg.node_selector:
            for _k, _v in cfg.node_selector.items():
                spark_submit_cmd[1:1] = ["--conf", f"spark.kubernetes.node.selector.{_k}={_v}"]
        pod_templates = _pod_templates(cfg)
        if pod_templates:
            for role in pod_templates:
                spark_submit_cmd[1:1] = [
                    "--conf",
                    f"spark.kubernetes.{role}.podTemplateFile={_POD_TEMPLATE_DIR}/{role}.json",
                ]
        if cfg.image_pull_secrets:
            secrets_csv = ",".join(cfg.image_pull_secrets)
            spark_submit_cmd[1:1] = [
                "--conf",
                f"spark.kubernetes.container.image.pullSecrets={secrets_csv}",
            ]

        credentials_secret_name: str | None = None
        core_api = None
        if cfg.s3_access_key and cfg.s3_secret_key:
            # secretKeyRef, not a literal --conf value: the pod spec's command array is
            # always visible via `kubectl get pod -o yaml` regardless of RBAC on Secrets,
            # so the raw credential must never appear there -- only the Secret's name does.
            # The native (Go) engine needs the same source-configured S3 credentials (the
            # node's ambient IAM role alone isn't sufficient here, confirmed live 2026-07-21).
            credentials_secret_name = f"{k8s_job_name}-aws"
            core_api = self._core_client()
            core_api.create_namespaced_secret(
                namespace=cfg.namespace,
                body={  # type: ignore[arg-type]  # the client serializes plain-dict manifests
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": credentials_secret_name,
                        "namespace": cfg.namespace,
                        "labels": spark_job_labels,
                    },
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
                "labels": spark_job_labels,
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
                        "labels": spark_job_labels,
                        "annotations": {"karpenter.sh/do-not-disrupt": "true"},
                    },
                    "spec": {
                        "restartPolicy": "Never",
                        "serviceAccountName": cfg.service_account,
                        "nodeSelector": cfg.node_selector,
                        "imagePullSecrets": [{"name": s} for s in cfg.image_pull_secrets],
                        "securityContext": pod_security_context(),
                        "containers": [
                            {
                                "name": "spark-submit",
                                "image": cfg.image,
                                "imagePullPolicy": cfg.image_pull_policy,
                                "securityContext": container_security_context(),
                                "command": spark_submit_cmd,
                                "resources": {
                                    "requests": dict(_WRAPPER_REQUESTS),
                                    "limits": {"cpu": "500m", "memory": "1Gi"},
                                },
                            },
                        ],
                    },
                },
            },
        }
        if pod_templates:
            _attach_pod_templates(job_manifest["spec"]["template"]["spec"], pod_templates, cfg)

        batch_api = self._batch_client()
        created_job = batch_api.create_namespaced_job(
            body=job_manifest,  # type: ignore[arg-type]  # the client serializes plain-dict manifests
            namespace=cfg.namespace,
        )
        if credentials_secret_name and core_api is not None:
            _adopt_credentials_secret(
                core_api,
                secret_name=credentials_secret_name,
                namespace=cfg.namespace,
                job_name=k8s_job_name,
                created_job=created_job,
            )
        logger.info(
            "Created Spark-on-K8s dispatch Job %s (driver=%s)", k8s_job_name, driver_pod_name
        )
        return k8s_job_name

    def get_job_run(self, job_id: str) -> JobRunResult:
        """Authoritative state comes from the driver pod, not the dispatch Job.

        spark-submit's own exit code does not reliably mirror the driver's outcome --
        confirmed live against example-cluster (2026-07-21): a driver pod whose SQL
        failed (container exit 1) still left the wrapping Job's spark-submit container at
        exit 0, because spark-submit's post-completion cleanup (deleting the driver's
        service/pods) threw its own RBAC exceptions in a background thread that never
        affected the main process's exit code. Trusting the wrapper Job's status alone
        silently reports SUCCESS for a run whose SQL actually failed -- always check the
        driver pod's own terminal phase instead.
        """
        driver_pod_name = f"{job_id}-driver"
        core_api = self._core_client()
        from kubernetes.client.exceptions import ApiException

        try:
            pod = core_api.read_namespaced_pod(
                name=driver_pod_name, namespace=self._config.namespace
            )
        except ApiException as exc:
            if exc.status == 404:
                # Driver pod not created yet (still starting) or submission failed before
                # spark-submit ever created it -- the wrapper Job is the only signal we have.
                return self._get_wrapper_job_run(job_id)
            raise

        phase = pod.status.phase if pod.status else None
        if phase == "Succeeded":
            detail = _read_pod_log_tail(driver_pod_name, self._config.namespace)
            from cryo.engines.spark.procedure_metrics import (
                bytes_removed_from_log,
                result_metrics_from_log,
            )

            return JobRunResult(
                job_run_id=job_id,
                state=JobRunState.SUCCESS,
                bytes_removed=bytes_removed_from_log(detail),
                metrics=result_metrics_from_log(detail),
            )
        if phase == "Failed":
            # The stored tail is still 2000 lines. Executor loss and the rewrite pool
            # can sit further up than that, above a long NullPointerException stack,
            # and truncation then keeps only the NPE (#425). Scan a wider tail for
            # those lines; only the lead and the last 2000 lines are returned.
            wide = _read_pod_log_tail(
                driver_pod_name,
                self._config.namespace,
                tail_lines=_FAILURE_SCAN_LINES,
            )
            detail = last_log_lines(wide, _LOG_TAIL_LINES)
            # A cgroup SIGKILL leaves INFO in the driver log and the reason on the
            # container status. truncate_error_message keeps the head, so the reason
            # has to be in front or the UI shows broadcast lines and the retry cannot
            # tell an OOM from a SQL error.
            reason = _container_termination_reason(pod)
            if reason:
                detail = f"{reason}\n{detail}" if detail else reason
            else:
                # The driver survived, so look for what killed the job: an OOM-killed
                # executor (Spark's follow-on NPE is not the cause, issue #166), else the
                # driver container's own exit when the log tail holds no exception at all.
                lead = driver_failure_lead(wide)
                if lead is None and deepest_caused_by(detail) is None:
                    lead = _container_exit_summary(pod)
                if lead:
                    detail = f"{lead}\n{detail}" if detail else lead
            return JobRunResult(job_run_id=job_id, state=JobRunState.FAILED, state_details=detail)
        start_time = getattr(pod.status, "start_time", None) if pod.status else None
        return JobRunResult(
            job_run_id=job_id,
            state=JobRunState.RUNNING,
            started_at=start_time if isinstance(start_time, datetime) else None,
        )

    def _get_wrapper_job_run(self, job_id: str) -> JobRunResult:
        """Fallback when the driver pod is not there: before it exists (submission itself
        failed -- bad image, RBAC on the wrapper pod -- and spark-submit never created it),
        or after it was deleted (node garbage collection, an operator) while the wrapper Job
        is still within its TTL.

        A 404 here means both the driver pod (checked by the caller before this) and the
        wrapper Job itself are gone -- normal once ttlSecondsAfterFinished passes, but if
        nothing ever resolved this job before then (e.g. the caller lacked the RBAC to
        check earlier -- see fallback_sweep's docstring), the real outcome is permanently
        unrecoverable. UNKNOWN, not FAILED: we never actually observed a failure, only its
        absence now.
        """
        from kubernetes.client.exceptions import ApiException

        batch_api = self._batch_client()
        try:
            job = batch_api.read_namespaced_job(name=job_id, namespace=self._config.namespace)
        except ApiException as exc:
            if exc.status == 404:
                return JobRunResult(
                    job_run_id=job_id,
                    state=JobRunState.UNKNOWN,
                    state_details=(
                        "Dispatch Job and driver pod both gone (TTL-expired) before this "
                        "job's outcome was ever resolved"
                    ),
                )
            raise
        status = job.status
        if status is not None and status.failed and status.failed >= 1:
            detail = _read_pod_log_tail(
                job_id, self._config.namespace, label_selector=f"job-name={job_id}"
            )
            # Driver already gone: the wrapper watcher is the only place Spark prints
            # "termination reason: OOMKilled". Front-load the exact prefix the retry
            # matches. A log that already starts with the word OOMKilled but not
            # "OOMKilled exit " would otherwise miss the step-up.
            detail = _prefix_oomkilled(detail)
            return JobRunResult(job_run_id=job_id, state=JobRunState.FAILED, state_details=detail)
        if status is not None and status.succeeded and status.succeeded >= 1:
            return self._wrapper_succeeded_outcome(job_id)
        return JobRunResult(job_run_id=job_id, state=JobRunState.RUNNING)

    def _wrapper_succeeded_outcome(self, job_id: str) -> JobRunResult:
        """Wrapper Job succeeded but the driver pod is gone: read the driver's final status
        from the wrapper's own log.

        The wrapper's exit code alone is not the answer (see get_job_run). spark-submit's
        LoggingPodStatusWatcherImpl prints the driver's last phase and container exit code
        before it exits, so the outcome is still on record after the driver pod was deleted.
        """
        detail = _read_pod_log_tail(
            job_id, self._config.namespace, label_selector=f"job-name={job_id}"
        )
        phase, exit_codes = wrapper_watcher_final_status(detail)
        if phase == "Failed" or any(code != 0 for code in exit_codes):
            return JobRunResult(
                job_run_id=job_id,
                state=JobRunState.FAILED,
                state_details=_prefix_oomkilled(detail),
            )
        if phase == "Succeeded":
            return JobRunResult(job_run_id=job_id, state=JobRunState.SUCCESS)
        return JobRunResult(
            job_run_id=job_id,
            state=JobRunState.UNKNOWN,
            state_details=(
                "Dispatch Job succeeded but its Spark driver pod is gone and the wrapper log "
                f"has no final driver status (last phase: {phase or 'none'})"
            ),
        )

    def wait_for_completion(
        self, job_id: str, *, deadline_seconds: int | None = None
    ) -> JobRunResult:
        """Poll until the Job reaches a terminal state or this deadline passes.

        ``deadline_seconds`` should be the same capped, table-size-aware value used to
        submit the Job (see _capped_submit_config) -- without it, this falls back to the
        client's own uncapped config.active_deadline_seconds, which is NOT what governs
        the Kubernetes Job's actual activeDeadlineSeconds once submit_spark_sql capped it.
        Confirmed live, 2026-10-07: this fallback silently diverged from the real Job
        deadline whenever config and the cap happened to differ, raising TimeoutError at
        the wrong wall-clock point relative to when Kubernetes would actually kill the Job.
        """
        effective_deadline = (
            deadline_seconds
            if deadline_seconds is not None
            else self._config.active_deadline_seconds
        )
        deadline = time.monotonic() + effective_deadline
        poll_interval = 10.0
        while time.monotonic() < deadline:
            result = self.get_job_run(job_id)
            if result.state in TERMINAL_STATES:
                return result
            time.sleep(poll_interval)
        raise TimeoutError(f"Spark Job {job_id} did not complete within {effective_deadline}s")

    def cancel_job_run(self, job_id: str) -> None:
        batch_api = self._batch_client()
        try:
            batch_api.delete_namespaced_job(
                name=job_id,
                namespace=self._config.namespace,
                propagation_policy="Foreground",
            )
        finally:
            # Driver is not owned by the Job -- Foreground delete will not remove it.
            self.cleanup_after_run(job_id)

    def cleanup_driver_pod(self, job_id: str) -> None:
        """Delete the Spark driver pod left behind after the application finishes.

        Spark keeps completed drivers for log inspection and does not attach them as
        children of our wrapper Job, so Job TTL alone never reclaims them. Best-effort:
        missing/already-deleted is not an error.
        """
        driver_pod_name = f"{job_id}-driver"
        try:
            core_api = self._core_client()
            core_api.delete_namespaced_pod(
                name=driver_pod_name,
                namespace=self._config.namespace,
            )
            logger.info("Deleted Spark driver pod %s", driver_pod_name)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup only
            if _is_not_found(exc):
                return
            logger.warning(
                "Failed to delete Spark driver pod %s: %s",
                driver_pod_name,
                exc,
                exc_info=True,
            )

    def _driver_pod_phase(self, job_id: str) -> str | None:
        """Phase of ``{job_id}-driver``, or None when that pod does not exist.

        A read that fails for any other reason reports ``Unknown``: the pod may still
        be there, and deleting its credentials Secret would leave it Pending forever.
        """
        try:
            pod = self._core_client().read_namespaced_pod(
                name=f"{job_id}-driver", namespace=self._config.namespace
            )
        except Exception as exc:  # noqa: BLE001 - best-effort; missing pod is the normal case
            if _is_not_found(exc):
                return None
            logger.warning(
                "Failed to read Spark driver pod %s-driver: %s", job_id, exc, exc_info=True
            )
            return "Unknown"
        phase = getattr(getattr(pod, "status", None), "phase", None)
        return phase if isinstance(phase, str) else "Unknown"

    def _has_live_executors(self, job_id: str) -> bool:
        """True when an executor owned by ``{job_id}-driver`` is not Succeeded/Failed.

        A failed read counts as live: deleting the Secret under a running executor is the
        failure this guards against.
        """
        try:
            pods = self._core_client().list_namespaced_pod(
                namespace=self._config.namespace, label_selector=_EXECUTOR_POD_LABEL_SELECTOR
            )
        except Exception:  # noqa: BLE001 - best-effort; err towards keeping the Secret
            logger.warning("Failed to list Spark executor pods for %s", job_id, exc_info=True)
            return True
        driver_name = f"{job_id}{_DRIVER_POD_SUFFIX}"
        for pod in pods.items or []:
            owners = getattr(pod.metadata, "owner_references", None) or []
            if not any(getattr(o, "name", None) == driver_name for o in owners):
                continue
            if getattr(pod.status, "phase", None) not in _TERMINAL_POD_PHASES:
                return True
        return False

    def cleanup_credentials_secret(self, job_id: str) -> None:
        """Delete the per-job AWS credentials Secret created by submit_spark_sql, if any.

        Best-effort: called after the run reaches a terminal state (or times out/raises)
        so credential Secrets don't accumulate. Missing/already-deleted is not an error.
        Prefers the Job annotation; falls back to the conventional ``{job_id}-aws`` name
        when the wrapper Job is already gone (TTL).

        A driver that exists and is not Succeeded or Failed still has to mount this
        Secret. Deleting it first leaves the pod Pending in CreateContainerConfigError
        with its CPU and memory reserved, and the stale-pod sweep used to ignore
        Pending (#423).
        """
        phase = self._driver_pod_phase(job_id)
        if phase in {"Pending", "Running", "Unknown"}:
            logger.info(
                "Keeping credentials Secret for %s; driver pod is %s",
                job_id,
                phase,
            )
            return
        if self._has_live_executors(job_id):
            # Executors mount the same Secret and outlive a deleted driver until garbage
            # collection removes them; the stale-pod sweep deletes the Secret later (#433).
            logger.info("Keeping credentials Secret for %s; executor pods still live", job_id)
            return
        secret_name: str | None = None
        try:
            batch_api = self._batch_client()
            job = batch_api.read_namespaced_job(name=job_id, namespace=self._config.namespace)
            annotations = job.metadata.annotations if job.metadata else None
            secret_name = (annotations or {}).get("cryo/credentials-secret-name")
        except Exception as exc:  # noqa: BLE001 - Job may already be TTL-deleted
            if not _is_not_found(exc):
                logger.warning(
                    "Failed to read Job %s while cleaning credentials Secret: %s",
                    job_id,
                    exc,
                    exc_info=True,
                )
        if not secret_name:
            secret_name = f"{job_id}-aws"
        try:
            core_api = self._core_client()
            core_api.delete_namespaced_secret(name=secret_name, namespace=self._config.namespace)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup only
            if _is_not_found(exc):
                return
            logger.warning("Failed to clean up credentials Secret for job %s", job_id)

    def cleanup_after_run(self, job_id: str) -> None:
        """Reclaim driver pod + credentials Secret after a terminal (or cancelled) run.

        The phase is checked before the driver pod is deleted. Deleting the pod first
        made the Secret look unused, and spark-submit can create the driver after that.
        """
        phase = self._driver_pod_phase(job_id)
        if phase in {"Pending", "Running", "Unknown"}:
            logger.info(
                "Not cleaning Spark job %s; driver pod is %s and still needs its credentials",
                job_id,
                phase,
            )
            return
        self.cleanup_driver_pod(job_id)
        self.cleanup_credentials_secret(job_id)


def driver_pressure_confs(
    operation: str | None,
    *,
    total_files: int | None,
    oom_steps: int = 0,
) -> dict[str, str]:
    """Confs for expire/orphan only. Other ops must not inherit the broadcast disable.

    Iceberg 1.11 ExpireSnapshotsSparkAction.expireFiles anti-joins every retained file
    against the delete candidates. Users who got past the driver kill needed
    ``spark.sql.autoBroadcastJoinThreshold=-1`` (ICEBERG-3703, ICEBERG-13737). That does
    not shrink the anti-join. ``snapshot_ids`` would not either: the valid-file side is
    built with a null id set. Scan stats have a file count, not snapshot ids, and the
    planner must not read the catalog to get them.

    Above 200k files one CALL still builds that full anti-join. The heap ceiling is the
    stop, not a second CALL. Scan stats have no snapshot ids to split on, and splitting
    would not shrink the retained-file side anyway.
    """
    if operation not in {"expire_snapshots", "remove_orphan_files"}:
        return {}
    heap = lightweight_driver_heap_mib(total_files=total_files, oom_steps=oom_steps)
    return {
        "spark.sql.autoBroadcastJoinThreshold": "-1",
        "spark.driver.maxResultSize": f"{driver_max_result_size_mib(heap)}m",
    }


def _merge_confs(*parts: Mapping[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for part in parts:
        merged.update(part)
    return merged


def catalog_io_confs(catalog: str, warehouse: str | None) -> dict[str, str]:
    """Spark confs that tune the catalog's S3FileIO; empty unless the warehouse is on S3.

    cryo_spark_runner.py only switches the catalog to S3FileIO for s3:// and s3a://
    warehouses, so these keys mean nothing anywhere else. S3FileIO bulk-deletes expired and
    orphaned files in DeleteObjects batches of 250 by default; 1000 is the S3 API maximum.
    Its retry policy only retries throttling, 5xx and I/O errors (2-20 s backoff); Iceberg's
    AWS docs recommend 32 attempts over the default 5 so a throttled rewrite waits for S3 to
    scale instead of failing.
    """
    if not warehouse or not warehouse.startswith(("s3://", "s3a://")):
        return {}
    prefix = f"spark.sql.catalog.{catalog}"
    return {f"{prefix}.s3.delete.batch-size": "1000", f"{prefix}.s3.retry.num-retries": "32"}


def _capped_submit_config(
    cfg: SparkK8sConfig,
    *,
    operation: str | None,
    total_size_gb: float | None = None,
    total_files: int | None = None,
    oom_steps: int = 0,
    executor_oom_steps: int = 0,
) -> SparkK8sConfig:
    profile = cap_dispatch_profile(
        ExecutorProfile(
            executor_instances=cfg.executor_instances,
            executor_cpu_milli=cfg.executor_cpu_milli,
            executor_mem_mib=cfg.executor_mem_mib,
            driver_cpu_milli=cfg.driver_cpu_milli,
            driver_mem_mib=cfg.driver_mem_mib,
        ),
        operation=operation,
        total_size_gb=total_size_gb,
        total_files=total_files,
        oom_steps=oom_steps,
        executor_oom_steps=executor_oom_steps,
    )
    return replace(
        cfg,
        executor_instances=profile.executor_instances,
        executor_cpu_milli=profile.executor_cpu_milli,
        executor_mem_mib=profile.executor_mem_mib,
        driver_cpu_milli=profile.driver_cpu_milli,
        driver_mem_mib=profile.driver_mem_mib,
        active_deadline_seconds=cap_deadline_seconds_for_table(
            cfg.active_deadline_seconds, total_size_gb=total_size_gb
        ),
    )


def _fold_local_driver(cfg: SparkK8sConfig) -> SparkK8sConfig:
    # executor_instances stays as is: if the runner's local master were ever ignored, the
    # job still gets its executor instead of hanging with none.
    folded = fold_into_local_driver(
        ExecutorProfile(
            executor_instances=cfg.executor_instances,
            executor_cpu_milli=cfg.executor_cpu_milli,
            executor_mem_mib=cfg.executor_mem_mib,
            driver_cpu_milli=cfg.driver_cpu_milli,
            driver_mem_mib=cfg.driver_mem_mib,
        )
    )
    return replace(
        cfg, driver_cpu_milli=folded.driver_cpu_milli, driver_mem_mib=folded.driver_mem_mib
    )
def _driver_request_milli(cfg: SparkK8sConfig) -> int:
    fraction = min(1.0, max(0.0, cfg.driver_cpu_request_fraction))
    wanted = max(_MIN_DRIVER_CPU_REQUEST_MILLI, round(cfg.driver_cpu_milli * fraction))
    return min(cfg.driver_cpu_milli, wanted)


_DRIVER_POD_LABEL_SELECTOR = "spark-role=driver"
_EXECUTOR_POD_LABEL_SELECTOR = "spark-role=executor"
_TERMINAL_POD_PHASES = frozenset({"Succeeded", "Failed"})
_DRIVER_POD_SUFFIX = "-driver"
_WRAPPER_JOB_LABEL_SELECTOR = "app.kubernetes.io/component=spark-fallback"


def _wrapper_jobs(
    batch_api: client.BatchV1Api, namespace: str
) -> tuple[frozenset[str], frozenset[str]]:
    """``(present, finished)`` names of Spark dispatch Jobs.

    A finished Job stays in ``present`` until its TTL deletes it. Completed drivers
    must wait for that (#377). A Pending driver whose Job has already finished will
    never start.
    """
    jobs = batch_api.list_namespaced_job(
        namespace=namespace, label_selector=_WRAPPER_JOB_LABEL_SELECTOR
    )
    present: set[str] = set()
    finished: set[str] = set()
    for job in jobs.items or []:
        name = job.metadata.name if job.metadata else None
        if not name:
            continue
        present.add(name)
        status = getattr(job, "status", None)
        succeeded = int(getattr(status, "succeeded", 0) or 0)
        failed = int(getattr(status, "failed", 0) or 0)
        if succeeded >= 1 or failed >= 1 or getattr(status, "completion_time", None) is not None:
            finished.add(name)
    return frozenset(present), frozenset(finished)


def _live_wrapper_job_names(batch_api: client.BatchV1Api, namespace: str) -> frozenset[str]:
    present, _finished = _wrapper_jobs(batch_api, namespace)
    return present


def cleanup_stale_driver_pods(
    *,
    namespace: str,
    older_than_seconds: int = _DEFAULT_TTL_SECONDS,
    core_api: client.CoreV1Api | None = None,
    batch_api: client.BatchV1Api | None = None,
) -> int:
    """Delete completed Spark driver pods older than ``older_than_seconds``.

    This module's own top-of-file docstring describes the intended cleanup design:
    "this dispatcher deletes the driver pod as part of Job TTL cleanup, which
    cascades [to executors]." That assumes the driver pod is owned by (and thus
    gets deleted alongside) the wrapper dispatch Job's ``ttlSecondsAfterFinished``.
    It isn't: cluster-mode ``spark-submit`` creates the driver pod itself via
    Spark's own Kubernetes scheduler backend, with no owner reference back to
    Cryo's wrapper Job -- so the TTL controller's cascade-delete-on-owner never
    reaches it. Confirmed live, 2026-08-06: 3,276 completed driver pods had
    accumulated with zero owner references (going back to 2026-08-04), against
    only 1 leftover executor pod -- executors genuinely are cleaned up by Spark
    itself on application completion, matching the docstring; only the driver
    side of that assumption was ever actually implemented by Spark.

    Restores the intended TTL behavior via an explicit sweep instead of relying
    on an ownership cascade that doesn't exist for cluster-mode driver pods.
    Best-effort: a failure listing or deleting one pod is logged and skipped,
    never raised -- this must not be able to break the governor tick it runs in.

    A driver whose wrapper Job still exists is never deleted, whatever its age. The
    driver pod is where get_job_run reads the outcome, and the age is measured from the
    driver's start while the Job lives until ``ttlSecondsAfterFinished`` after it ends.
    Deleting on age alone removed drivers of runs nobody had resolved yet (an async
    fallback dispatch waits for the scheduler's sweep), and the run was then recorded
    FAILED from the wrapper Job alone (issue #377). When the Job list cannot be read,
    nothing is deleted this pass.

    A Running driver whose wrapper Job is gone is deleted too, once it is older than
    ``older_than_seconds``. The Job holds the outcome; without it nobody resolves the
    driver, and one stuck requesting an executor ran for days holding its reservation
    (#433). Its executors are owned by the driver pod and go with it. Whenever a driver
    is deleted because its Job is gone, its ``{job}-aws`` credentials Secret is deleted
    as well, since no one else is left to clean it up.
    """
    api = core_api or _new_k8s_core_client()
    jobs_api = batch_api or _new_k8s_batch_client()
    try:
        live_wrappers, finished_wrappers = _wrapper_jobs(jobs_api, namespace)
    except Exception:  # noqa: BLE001 - best-effort cleanup only
        logger.warning(
            "Failed to list Spark dispatch Jobs; skipping driver pod cleanup", exc_info=True
        )
        return 0
    deleted = 0
    now = time.time()
    for phase in ("Succeeded", "Failed", "Pending", "Running"):
        try:
            pods = api.list_namespaced_pod(
                namespace=namespace,
                label_selector=_DRIVER_POD_LABEL_SELECTOR,
                field_selector=f"status.phase={phase}",
            )
        except Exception:  # noqa: BLE001 - best-effort cleanup only
            logger.warning("Failed to list %s Spark driver pods for cleanup", phase, exc_info=True)
            continue
        for pod in pods.items or []:
            pod_name = pod.metadata.name if pod.metadata else None
            if not pod_name or not pod_name.endswith(_DRIVER_POD_SUFFIX):
                continue
            job_name = pod_name.removesuffix(_DRIVER_POD_SUFFIX)
            if phase == "Pending":
                # A container that never started has no start_time. Age is from the
                # pod object, which exists as soon as spark-submit creates it.
                stamp = getattr(pod.metadata, "creation_timestamp", None)
            else:
                stamp = getattr(pod.status, "start_time", None)
            if stamp is None:
                continue
            if now - stamp.timestamp() < older_than_seconds:
                continue
            if phase == "Pending":
                # Still starting, and its Secret is there: leave it. A finished or
                # deleted wrapper will never start it, and a missing Secret cannot.
                if job_name in live_wrappers and job_name not in finished_wrappers:
                    if not _credentials_secret_missing(api, namespace, f"{job_name}-aws"):
                        continue
            elif job_name in live_wrappers:
                continue
            try:
                api.delete_namespaced_pod(name=pod_name, namespace=namespace)
                deleted += 1
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup only
                if not _is_not_found(exc):
                    logger.warning("Failed to delete stale Spark driver pod %s: %s", pod_name, exc)
                    continue
            if job_name not in live_wrappers:
                _delete_credentials_secret(api, namespace, f"{job_name}-aws")
    return deleted


def _delete_credentials_secret(api: Any, namespace: str, name: str) -> None:
    try:
        api.delete_namespaced_secret(name=name, namespace=namespace)
    except Exception as exc:  # noqa: BLE001 - best-effort cleanup only
        if not _is_not_found(exc):
            logger.warning("Failed to delete Spark credentials Secret %s: %s", name, exc)


def _credentials_secret_missing(api: Any, namespace: str, name: str) -> bool:
    """True only on a confirmed 404. Any other error keeps the driver."""
    try:
        api.read_namespaced_secret(name=name, namespace=namespace)
    except Exception as exc:  # noqa: BLE001 - best-effort cleanup only
        return _is_not_found(exc)
    return False


_k8s_config_loaded = False
_module_client_lock = threading.Lock()
_module_batch_client: client.BatchV1Api | None = None
_module_core_client: client.CoreV1Api | None = None


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
    """Process-wide cached singleton, not a fresh ApiClient per call.

    SparkK8sClient already caches these per-instance (see its _batch_client/_core_client
    methods), but that cache is defeated whenever a fresh SparkK8sClient gets built --
    which is routine, not rare: cryo.governor.native_admit._dispatch_fallback_operations
    calls create_engine() (building a brand-new SparkK8sEngine -> SparkK8sClient) once
    PER TABLE, and _run_fallback_dispatches_concurrently runs up to 6 of those
    concurrently via a ThreadPoolExecutor -- confirmed live, 2026-08-14: cryo-worker pods
    (4Gi limit) OOMKilled repeatedly, idle at ~160-225Mi but spiking hard during exactly
    this "Created Spark-on-K8s dispatch Job ... Deleted Spark driver pod" burst pattern
    in the logs. Each fresh ApiClient (constructed inside BatchV1Api()/CoreV1Api() when
    none is passed) owns its own urllib3.PoolManager + SSL context + certifi bundle --
    real, non-trivial memory -- and a per-table engine has no legitimate reason to also
    mean a per-table K8s API connection: the in-cluster K8s API server is the same
    regardless of which table's Spark job is being dispatched, only the *Spark* config
    (S3 credentials, catalog URI) is legitimately per-table (see
    native_admit._fallback_engine_config's docstring for why that part can't be cached).
    Lock-guarded since this is reached from real worker threads, not just sequentially.
    """
    global _module_batch_client
    if _module_batch_client is None:
        with _module_client_lock:
            if _module_batch_client is None:
                from kubernetes import client as k8s_client_module

                _ensure_k8s_config()
                _module_batch_client = k8s_client_module.BatchV1Api()
    return _module_batch_client


def _new_k8s_core_client() -> client.CoreV1Api:
    """Process-wide cached singleton -- see _new_k8s_batch_client's docstring."""
    global _module_core_client
    if _module_core_client is None:
        with _module_client_lock:
            if _module_core_client is None:
                from kubernetes import client as k8s_client_module

                _ensure_k8s_config()
                _module_core_client = k8s_client_module.CoreV1Api()
    return _module_core_client


def _is_not_found(exc: BaseException) -> bool:
    status = getattr(exc, "status", None)
    if status == 404:
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, str) and "not found" in body.lower():
        return True
    return False


def _adopt_credentials_secret(
    core_api: Any,
    *,
    secret_name: str,
    namespace: str,
    job_name: str,
    created_job: Any,
) -> None:
    """Attach the AWS credentials Secret as a child of the wrapper Job.

    Job TTL then cascades Secret deletion even if ``cleanup_credentials_secret``
    never runs (e.g. fire-and-forget admin diagnostics). The Spark driver pod
    still needs an explicit delete -- Spark creates it without ownerReferences.
    """
    metadata = getattr(created_job, "metadata", None)
    uid = getattr(metadata, "uid", None)
    if not uid:
        logger.warning(
            "Created Job %s without UID; credentials Secret needs explicit cleanup",
            job_name,
        )
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
    except Exception:  # noqa: BLE001 - best-effort; cleanup_after_run still deletes
        logger.warning("Failed to attach credentials Secret to Job %s", job_name)


_LOG_TAIL_LINES = 2000
# A SparkPlan.session() NPE stack can push the executor-loss lines, and the
# Rewrite-Service frame that shows the rewrite pool, out of the stored tail.
_FAILURE_SCAN_LINES = 20_000


def _read_pod_log_tail(
    pod_name: str,
    namespace: str,
    *,
    label_selector: str | None = None,
    tail_lines: int = _LOG_TAIL_LINES,
) -> str | None:
    """Best-effort: pull a tail of a pod's logs for the error.

    Direct name lookup by default (used for the driver pod, whose name is known);
    falls back to a label-selector list when the caller only knows the owning Job
    (the wrapper-pod fallback path, where the pod's generated suffix isn't known).

    Regression, 2026-07-31: this used `limit_bytes=1024*8` then took the last 20 lines of
    *that* -- but `limit_bytes` on the Kubernetes pod-log API returns bytes from the START
    of the stream, not the end. For any Spark driver with more than ~8KB of startup/executor-
    allocation INFO chatter before the real failure (i.e. almost every real failure), the
    captured "tail" was 20 lines of startup noise nowhere near the actual exception -- e.g. a
    NoSuchTableException that killed the job never appeared in job.error at all, only
    "Decommissioning not enabled, skipping shutdown script" spam. `tail_lines` is the
    correct parameter for "last N lines of a pod's log" and needs no byte-size guessing.

    Regression, , confirmed live 2026-08-18: 200 lines still wasn't enough. A
    real sample: 19 of 47 truncated MaintenanceJob.error rows over 14 days had no exception
    message at all captured -- only bare `at org.apache.spark...` stack frames or trailing
    "Shutting down all executors" shutdown chatter, because the real `Exception: message`
    header line Spark prints was further back than 200 lines from the end (e.g. Spark's own
    graceful-shutdown log sequence after a failure routinely runs 40-60 more INFO lines).
    2000 lines costs nothing extra downstream -- this is only the *capture* window; the
    value that actually reaches Postgres/API responses is still capped to
    ERROR_MESSAGE_TRUNCATE (4000 chars) by truncate_error_message, and deepest_caused_by
    (see cryo.engines.base) searches this whole captured tail for the real signature before
    that cap is applied, regardless of how much log text sits in between.
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
            name = pod.metadata.name if pod.metadata else None
            if not name:
                continue
            try:
                logs: str = core_api.read_namespaced_pod_log(
                    name=name,
                    namespace=namespace,
                    tail_lines=tail_lines,
                )
                tail = logs.strip()
                if tail:
                    return tail
            except Exception:  # noqa: BLE001 - best-effort diagnostics only
                continue
    except Exception:  # noqa: BLE001 - best-effort diagnostics only
        pass
    return None


def _container_termination_reason(pod: Any) -> str | None:
    """Kernel reason from containerStatuses, not from the log.

    OOMKilled is a cgroup event. The JVM does not print it. Spark's watcher prints
    ``termination reason: OOMKilled`` on the wrapper, which we do not read while the
    driver pod still exists. Read the status the kubelet already recorded.

    Exit 137 alone is not enough. Eviction and preemption also SIGKILL. Only a
    recorded reason of OOMKilled may grow the next driver.
    """
    statuses = getattr(getattr(pod, "status", None), "container_statuses", None) or []
    for status in statuses:
        terminated = getattr(getattr(status, "state", None), "terminated", None)
        if terminated is None:
            continue
        reason = getattr(terminated, "reason", None)
        if reason != "OOMKilled":
            continue
        exit_code = getattr(terminated, "exit_code", None)
        code = exit_code if exit_code is not None else 137
        return f"OOMKilled exit {code}"
    return None


EXECUTOR_OOM_PREFIX = "Executor OOMKilled"
# Spark's ExecutorPodsLifecycleManager, in the driver log, for a cgroup-killed executor:
#   The executor with id 2 exited with exit code 137(SIGKILL, possible container OOM).
#   ... container name: spark-kubernetes-executor ... termination reason: OOMKilled
_EXECUTOR_EXIT_RE = re.compile(r"executor with id (\S+) exited with exit code (\d+)")
_LOST_EXECUTOR_RE = re.compile(r"Lost executor (\S+) on ")
_EXECUTOR_CONTAINER = "spark-kubernetes-executor"
_OOMKILLED_REASON = "termination reason: OOMKilled"


def executor_oom_summary(detail: str | None) -> str | None:
    """``Executor OOMKilled exit 137 (executor 2)`` when the driver log shows a killed executor.

    Requires the kubelet's recorded ``OOMKilled`` reason next to an executor container, the
    same evidence rule as the driver: exit 137 alone is also eviction or preemption.
    """
    if not detail or _OOMKILLED_REASON not in detail:
        return None
    lines = detail.splitlines()
    oom_lines = [i for i, line in enumerate(lines) if _OOMKILLED_REASON in line]
    executor_oom = any(
        any(_EXECUTOR_CONTAINER in line for line in lines[max(0, i - 40) : i + 1])
        for i in oom_lines
    )
    if not executor_oom:
        return None
    exit_match = _EXECUTOR_EXIT_RE.search(detail)
    if exit_match:
        return f"{EXECUTOR_OOM_PREFIX} exit {exit_match.group(2)} (executor {exit_match.group(1)})"
    lost = _LOST_EXECUTOR_RE.search(detail)
    if lost:
        return f"{EXECUTOR_OOM_PREFIX} exit 137 (executor {lost.group(1)})"
    return f"{EXECUTOR_OOM_PREFIX} exit 137"


def last_log_lines(log: str | None, count: int) -> str | None:
    """The last ``count`` lines, or the log itself when it is already shorter."""
    if not log or count < 1:
        return log
    lines = log.splitlines()
    if len(lines) <= count:
        return log
    return "\n".join(lines[-count:])


# Iceberg 1.11 submits each file group to a pool named Rewrite-Service-N
# (RewriteDataFilesSparkAction.rewriteService). SparkPlan.session is the active
# session captured when the plan object is built. A plan built on that pool,
# without the session set on the thread, is this NPE with no executor loss.
_SESSION_NPE = "SparkPlan.session()"
_FAILURE_CONTEXT_TOKENS = (
    _SESSION_NPE,
    "Lost executor",
    "exit code 137",
    _OOMKILLED_REASON,
    _EXECUTOR_CONTAINER,
    "Rewrite-Service-",
    "RewriteDataFiles",
)


def session_npe_context(detail: str | None, *, limit: int = 24) -> str | None:
    """Lines that tell a session NPE from an executor kill, from anywhere in the log."""
    if not detail or _SESSION_NPE not in detail:
        return None
    kept: list[str] = []
    seen: set[str] = set()
    for line in detail.splitlines():
        if not any(token in line for token in _FAILURE_CONTEXT_TOKENS):
            continue
        text = line.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        kept.append(text)
        if len(kept) >= limit:
            break
    return "\n".join(kept) if kept else None


def driver_failure_lead(detail: str | None) -> str | None:
    """What truncation must keep from a driver log wider than the stored tail.

    An executor OOM is the cause, and its summary has to be the first line so the
    retry still matches. A session NPE with no OOM keeps the rewrite-pool and
    executor-loss lines that sat above the stack (#425).
    """
    oom = executor_oom_summary(detail)
    context = session_npe_context(detail)
    if oom is None:
        return context
    if not context:
        return oom
    extra = [line for line in context.splitlines() if line != oom and _SESSION_NPE not in line]
    return f"{oom}\n" + "\n".join(extra) if extra else oom


def _container_exit_summary(pod: Any) -> str | None:
    """``driver container terminated: Error exit 1`` from the recorded container status."""
    statuses = getattr(getattr(pod, "status", None), "container_statuses", None) or []
    for status in statuses:
        terminated = getattr(getattr(status, "state", None), "terminated", None)
        if terminated is None:
            continue
        reason = getattr(terminated, "reason", None) or "terminated"
        exit_code = getattr(terminated, "exit_code", None)
        suffix = f" exit {exit_code}" if exit_code is not None else ""
        return f"driver container terminated: {reason}{suffix}"
    return None


# spark-submit's LoggingPodStatusWatcherImpl, in the wrapper pod log:
#   Application status for spark-<id> (phase: Succeeded)
#   Container final statuses:
#        container name: spark-kubernetes-driver
#        container state: terminated
#        exit code: 0
_WATCHER_PHASE_RE = re.compile(r"\(phase: (\w+)\)|^\s*phase: (\w+)\s*$", re.MULTILINE)
_WATCHER_FINAL_STATUSES = "Container final statuses:"
_WATCHER_EXIT_CODE_RE = re.compile(r"^\s*exit code: (-?\d+)\s*$", re.MULTILINE)


def wrapper_watcher_final_status(log: str | None) -> tuple[str | None, tuple[int, ...]]:
    """The driver's last reported pod phase and container exit codes from the wrapper log.

    Exit codes come from the watcher's final-statuses block when it printed one, otherwise
    from the last ``exit code:`` line. ``(None, ())`` when the log is missing or Spark never
    reported a status.
    """
    if not log:
        return None, ()
    phases = _WATCHER_PHASE_RE.findall(log)
    phase = (phases[-1][0] or phases[-1][1]) if phases else None
    final_at = log.rfind(_WATCHER_FINAL_STATUSES)
    if final_at >= 0:
        codes = tuple(int(c) for c in _WATCHER_EXIT_CODE_RE.findall(log, final_at))
    else:
        last = _WATCHER_EXIT_CODE_RE.findall(log)[-1:]
        codes = tuple(int(c) for c in last)
    return phase, codes


def _prefix_oomkilled(detail: str | None) -> str | None:
    """Put ``OOMKilled exit 137`` at the front when the wrapper log is the only signal.

    Matching the bare word anywhere would step up a SQL error that mentioned the
    watcher line. A log that already starts with the word but not the exit prefix
    still needs the prefix, or the retry predicate misses it.
    """
    if not detail or "OOMKilled" not in detail:
        return detail
    if detail.startswith("OOMKilled exit "):
        return detail
    return f"OOMKilled exit 137\n{detail}"
