"""Unit tests for Cryo's self-managed Spark-on-Kubernetes dispatch."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cryo.engines.spark.k8s import (
    JobRunState,
    SparkK8sClient,
    SparkK8sConfig,
)


def test_config_from_config_uses_nested_spark_k8s_block() -> None:
    cfg = SparkK8sConfig.from_config(
        {
            "spark_k8s": {
                "namespace": "cryo",
                "image": "cryo-spark:1.2.3",
                "service_account": "cryo-spark",
                "catalog_uri": "http://iceberg-rest:8181",
                "catalog_warehouse": "s3://warehouse/",
                "executor_instances": 4,
            }
        }
    )
    assert cfg.namespace == "cryo"
    assert cfg.image == "cryo-spark:1.2.3"
    assert cfg.service_account == "cryo-spark"
    assert cfg.catalog_uri == "http://iceberg-rest:8181"
    assert cfg.executor_instances == 4


def test_config_from_config_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYO_SPARK_K8S_NAMESPACE", "cryo")
    monkeypatch.setenv("CRYO_SPARK_K8S_IMAGE", "cryo-spark:latest")
    monkeypatch.setenv("CRYO_SPARK_K8S_SERVICE_ACCOUNT", "cryo-spark")
    monkeypatch.setenv("CRYO_SPARK_K8S_IMAGE_PULL_SECRETS", "cryo-registry,other")

    cfg = SparkK8sConfig.from_config({})
    assert cfg.namespace == "cryo"
    assert cfg.image == "cryo-spark:latest"
    assert cfg.service_account == "cryo-spark"
    assert cfg.image_pull_secrets == ("cryo-registry", "other")


def test_config_from_config_defaults() -> None:
    cfg = SparkK8sConfig.from_config({})
    assert cfg.namespace == "default"
    assert cfg.service_account == "default"
    assert cfg.image_pull_secrets == ()
    assert cfg.driver_mem_mib == 2048
    assert cfg.executor_instances == 2


def test_submit_spark_sql_creates_job_with_expected_shape() -> None:
    batch_api = MagicMock()
    cfg = SparkK8sConfig(
        namespace="cryo",
        image="cryo-spark:latest",
        service_account="cryo-spark",
        catalog_uri="http://iceberg-rest:8181",
        catalog_warehouse="s3://warehouse/",
        image_pull_secrets=("cryo-registry",),
    )
    client = SparkK8sClient(cfg)

    with patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api):
        job_id = client.submit_spark_sql(
            "CALL cdp.system.rewrite_manifests(table => 'ns.tbl')",
            job_name="cryo-optimize-manifests",
            catalog="cdp",
        )

    assert job_id.startswith("cryo-spark-")
    manifest = batch_api.create_namespaced_job.call_args.kwargs["body"]
    assert manifest["metadata"]["namespace"] == "cryo"
    pod_spec = manifest["spec"]["template"]["spec"]
    assert pod_spec["serviceAccountName"] == "cryo-spark"
    assert pod_spec["imagePullSecrets"] == [{"name": "cryo-registry"}]
    container = pod_spec["containers"][0]
    assert container["image"] == "cryo-spark:latest"
    command = container["command"]
    assert command[0] == "/opt/spark/bin/spark-submit"
    assert "--master" in command
    master_idx = command.index("--master")
    assert command[master_idx + 1] == "k8s://https://kubernetes.default.svc:443"
    assert "--deploy-mode" in command
    assert command[command.index("--deploy-mode") + 1] == "cluster"
    # SQL and catalog args reach the runner script, not swallowed by spark-submit confs.
    assert "--sql" in command
    assert command[command.index("--sql") + 1] == "CALL cdp.system.rewrite_manifests(table => 'ns.tbl')"
    assert "--catalog-uri" in command
    assert command[command.index("--catalog-uri") + 1] == "http://iceberg-rest:8181"


def test_get_job_run_maps_success_and_failure() -> None:
    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)
    batch_api = MagicMock()

    batch_api.read_namespaced_job.return_value = SimpleNamespace(
        status=SimpleNamespace(succeeded=1, failed=None)
    )
    with patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api):
        result = client.get_job_run("cryo-spark-abc123")
    assert result.state is JobRunState.SUCCESS

    batch_api.read_namespaced_job.return_value = SimpleNamespace(
        status=SimpleNamespace(succeeded=None, failed=1)
    )
    with (
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api),
        patch("cryo.engines.spark.k8s._read_failure_detail", return_value="boom"),
    ):
        result = client.get_job_run("cryo-spark-abc123")
    assert result.state is JobRunState.FAILED
    assert result.state_details == "boom"

    batch_api.read_namespaced_job.return_value = SimpleNamespace(
        status=SimpleNamespace(succeeded=None, failed=None)
    )
    with patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api):
        result = client.get_job_run("cryo-spark-abc123")
    assert result.state is JobRunState.RUNNING
