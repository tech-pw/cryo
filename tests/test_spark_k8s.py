"""Unit tests for Cryo's self-managed Spark-on-Kubernetes dispatch."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cryo.engines.spark.k8s import (
    JobRunState,
    SparkK8sClient,
    SparkK8sConfig,
    _read_pod_log_tail,
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
            destination_id="b377ac3f-7d7f-47f7-985d-7543cd4cd82c",
        )

    assert job_id.startswith("cryo-spark-")
    manifest = batch_api.create_namespaced_job.call_args.kwargs["body"]
    assert manifest["metadata"]["namespace"] == "cryo"
    # Move 6 (Spark side): the live capacity probe filters Jobs by this label -- without
    # it, LiveCapacityProvider can't distinguish this destination's in-flight Jobs from
    # any other Spark-K8s destination's.
    assert (
        manifest["metadata"]["labels"]["cryo/destination"] == "b377ac3f-7d7f-47f7-985d-7543cd4cd82c"
    )
    pod_spec = manifest["spec"]["template"]["spec"]
    assert pod_spec["serviceAccountName"] == "cryo-spark"
    assert pod_spec["imagePullSecrets"] == [{"name": "cryo-registry"}]
    # Dispatch pod must land on the same Karpenter on-demand pool as native-engine jobs --
    # that pool's instance profile is what actually has S3 access to the warehouse bucket.
    assert pod_spec["nodeSelector"] == {"karpenter.sh/capacity-type": "on-demand"}
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
    assert (
        command[command.index("--sql") + 1]
        == "CALL cdp.system.rewrite_manifests(table => 'ns.tbl')"
    )
    assert "--catalog-uri" in command
    assert command[command.index("--catalog-uri") + 1] == "http://iceberg-rest:8181"
    # Driver/executor pods (created by spark-submit itself, not our Job manifest) also
    # need the same node selector -- this conf flag is how we reach them.
    assert "spark.kubernetes.node.selector.karpenter.sh/capacity-type=on-demand" in command


def test_submit_spark_sql_labels_default_destination_when_none_given() -> None:
    batch_api = MagicMock()
    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)

    with patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api):
        client.submit_spark_sql("SELECT 1", catalog="cdp")

    manifest = batch_api.create_namespaced_job.call_args.kwargs["body"]
    assert manifest["metadata"]["labels"]["cryo/destination"] == "default"


def test_submit_repair_missing_files_defaults_to_dry_run() -> None:
    batch_api = MagicMock()
    cfg = SparkK8sConfig(namespace="cryo", catalog_impl="hive", catalog_uri="thrift://hms:9083")
    client = SparkK8sClient(cfg)

    with patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api):
        client.submit_repair_missing_files("lake.analytics.load_test_basefact")

    manifest = batch_api.create_namespaced_job.call_args.kwargs["body"]
    command = manifest["spec"]["template"]["spec"]["containers"][0]["command"]
    assert "--repair-missing-files" in command
    assert "--table" in command
    assert command[command.index("--table") + 1] == "lake.analytics.load_test_basefact"
    # Must match table_ref's catalog segment, or Spark can't resolve the reference at
    # all -- regression: omitting this left the runner's "glue_catalog" default
    # registered, and querying "cdp...." against it threw REQUIRES_SINGLE_PART_NAMESPACE.
    assert "--catalog" in command
    assert command[command.index("--catalog") + 1] == "cdp"
    assert "--commit" not in command


def test_submit_repair_missing_files_with_commit() -> None:
    batch_api = MagicMock()
    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)

    with patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api):
        client.submit_repair_missing_files("lake.analytics.load_test_basefact", commit=True)

    manifest = batch_api.create_namespaced_job.call_args.kwargs["body"]
    command = manifest["spec"]["template"]["spec"]["containers"][0]["command"]
    assert "--commit" in command


def test_submit_spark_sql_creates_credentials_secret_when_configured() -> None:
    """The node's ambient IAM role alone isn't enough to reach the S3 bucket (confirmed
    live, 2026-07-21) -- when source-provided S3 credentials are present, they must reach
    the driver/executor pods via a Secret + secretKeyRef, never as a literal --conf value
    (the pod spec's command array is always visible via `kubectl get pod -o yaml`)."""
    batch_api = MagicMock()
    core_api = MagicMock()
    cfg = SparkK8sConfig(
        namespace="cryo",
        s3_access_key="AKIA_TEST",
        s3_secret_key="shh_test",
    )
    client = SparkK8sClient(cfg)

    with (
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api),
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
    ):
        client.submit_spark_sql(
            "CALL cdp.system.rewrite_manifests(table => 'ns.tbl')", catalog="cdp"
        )

    secret_body = core_api.create_namespaced_secret.call_args.kwargs["body"]
    assert secret_body["stringData"]["access_key_id"] == "AKIA_TEST"
    assert secret_body["stringData"]["secret_access_key"] == "shh_test"
    secret_name = secret_body["metadata"]["name"]

    manifest = batch_api.create_namespaced_job.call_args.kwargs["body"]
    command = manifest["spec"]["template"]["spec"]["containers"][0]["command"]
    # Must precede the script path, not follow it (spark-submit parses everything after
    # the script as the script's own args).
    script_idx = command.index("local:///opt/cryo/cryo_spark_runner.py")
    driver_conf = (
        f"spark.kubernetes.driver.secretKeyRef.AWS_ACCESS_KEY_ID={secret_name}:access_key_id"
    )
    assert driver_conf in command
    assert command.index(driver_conf) < script_idx
    assert manifest["metadata"]["annotations"]["cryo/credentials-secret-name"] == secret_name


def test_cleanup_credentials_secret_deletes_when_annotation_present() -> None:
    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)
    batch_api = MagicMock()
    batch_api.read_namespaced_job.return_value = SimpleNamespace(
        metadata=SimpleNamespace(annotations={"cryo/credentials-secret-name": "cryo-spark-abc-aws"})
    )
    core_api = MagicMock()

    with (
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api),
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
    ):
        client.cleanup_credentials_secret("cryo-spark-abc")

    core_api.delete_namespaced_secret.assert_called_once_with(
        name="cryo-spark-abc-aws", namespace="cryo"
    )


def test_cleanup_credentials_secret_noop_without_annotation() -> None:
    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)
    batch_api = MagicMock()
    batch_api.read_namespaced_job.return_value = SimpleNamespace(
        metadata=SimpleNamespace(annotations={})
    )
    core_api = MagicMock()

    with (
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api),
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
    ):
        client.cleanup_credentials_secret("cryo-spark-abc")

    core_api.delete_namespaced_secret.assert_not_called()


def test_read_pod_log_tail_requests_tail_lines_not_a_byte_prefix() -> None:
    """Regression, 2026-07-31: limit_bytes returns bytes from the START of the pod log, not
    the end -- for any driver with more than ~8KB of startup chatter before the real failure,
    the old code captured 20 lines of noise nowhere near the actual exception. A
    NoSuchTableException live in production never reached job.error because of this -- only
    "Decommissioning not enabled" spam did. tail_lines is the correct API parameter for
    "last N lines"."""
    core_api = MagicMock()
    core_api.read_namespaced_pod.return_value = SimpleNamespace(
        metadata=SimpleNamespace(name="cryo-spark-abc123-driver")
    )
    core_api.read_namespaced_pod_log.return_value = (
        "INFO noise line 1\n" * 500 + "Caused by: NoSuchTableException: table not found"
    )

    with patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api):
        tail = _read_pod_log_tail("cryo-spark-abc123-driver", "cryo")

    core_api.read_namespaced_pod_log.assert_called_once_with(
        name="cryo-spark-abc123-driver", namespace="cryo", tail_lines=200
    )
    assert "NoSuchTableException" in tail
    assert "limit_bytes" not in core_api.read_namespaced_pod_log.call_args.kwargs


def test_get_job_run_reads_driver_pod_not_wrapper_job() -> None:
    """The wrapper Job's spark-submit container can exit 0 even when the driver pod's
    SQL failed (spark-submit's own cleanup swallows the real failure in a background
    thread) -- get_job_run must trust the driver pod's phase, not the wrapper Job.
    Regression for a live incident on data-platform-cluster, 2026-07-21."""
    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)
    core_api = MagicMock()

    core_api.read_namespaced_pod.return_value = SimpleNamespace(
        status=SimpleNamespace(phase="Succeeded")
    )
    with patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api):
        result = client.get_job_run("cryo-spark-abc123")
    assert result.state is JobRunState.SUCCESS
    core_api.read_namespaced_pod.assert_called_with(
        name="cryo-spark-abc123-driver", namespace="cryo"
    )

    core_api.read_namespaced_pod.return_value = SimpleNamespace(
        status=SimpleNamespace(phase="Failed")
    )
    with (
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
        patch("cryo.engines.spark.k8s._read_pod_log_tail", return_value="boom"),
    ):
        result = client.get_job_run("cryo-spark-abc123")
    assert result.state is JobRunState.FAILED
    assert result.state_details == "boom"

    core_api.read_namespaced_pod.return_value = SimpleNamespace(
        status=SimpleNamespace(phase="Running")
    )
    with patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api):
        result = client.get_job_run("cryo-spark-abc123")
    assert result.state is JobRunState.RUNNING


def test_get_job_run_falls_back_to_wrapper_job_when_driver_pod_missing() -> None:
    from kubernetes.client.exceptions import ApiException

    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)
    core_api = MagicMock()
    core_api.read_namespaced_pod.side_effect = ApiException(status=404)
    batch_api = MagicMock()

    # Driver pod never created and the wrapper Job itself failed (e.g. bad image).
    batch_api.read_namespaced_job.return_value = SimpleNamespace(
        status=SimpleNamespace(succeeded=None, failed=1)
    )
    with (
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api),
        patch("cryo.engines.spark.k8s._read_pod_log_tail", return_value="image pull error"),
    ):
        result = client.get_job_run("cryo-spark-abc123")
    assert result.state is JobRunState.FAILED
    assert result.state_details == "image pull error"

    # Wrapper Job "succeeded" but we never observed a driver pod -- don't trust that.
    batch_api.read_namespaced_job.return_value = SimpleNamespace(
        status=SimpleNamespace(succeeded=1, failed=None)
    )
    with (
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api),
    ):
        result = client.get_job_run("cryo-spark-abc123")
    assert result.state is JobRunState.FAILED
    assert "never observed" in (result.state_details or "")

    # Still starting -- neither succeeded nor failed yet.
    batch_api.read_namespaced_job.return_value = SimpleNamespace(
        status=SimpleNamespace(succeeded=None, failed=None)
    )
    with (
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api),
    ):
        result = client.get_job_run("cryo-spark-abc123")
    assert result.state is JobRunState.RUNNING
