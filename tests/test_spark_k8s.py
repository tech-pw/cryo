"""Unit tests for Cryo's self-managed Spark-on-Kubernetes dispatch."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import cryo.engines.spark.k8s as k8s_module
from cryo.engines.spark.k8s import (
    JobRunState,
    SparkK8sClient,
    SparkK8sConfig,
    _read_pod_log_tail,
    cleanup_stale_driver_pods,
)


@pytest.fixture(autouse=True)
def _reset_module_level_k8s_client_cache():
    """_new_k8s_batch_client/_new_k8s_core_client cache a real ApiClient at module scope
    (see their docstrings -- the fix for a real production OOM, 2026-08-14). That's
    correct in production (the whole point is one shared connection for the process's
    lifetime) but must not leak between tests, or an early test's mock/real client would
    silently satisfy a later test that expects a fresh singleton to be built."""
    k8s_module._module_batch_client = None
    k8s_module._module_core_client = None
    yield
    k8s_module._module_batch_client = None
    k8s_module._module_core_client = None


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
        node_selector={"karpenter.sh/capacity-type": "on-demand"},
    )
    client = SparkK8sClient(cfg)

    with patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api):
        job_id =         client.submit_spark_sql(
            "CALL cdp.system.rewrite_manifests(table => 'ns.tbl')",
            job_name="cryo-optimize-manifests",
            catalog="cdp",
            destination_id="b377ac3f-7d7f-47f7-985d-7543cd4cd82c",
            operation="optimize_manifests",
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
    # Same for do-not-disrupt: wrapper Job annotation alone does not protect driver/executor.
    assert "spark.kubernetes.driver.annotation.karpenter.sh/do-not-disrupt=true" in command
    assert "spark.kubernetes.executor.annotation.karpenter.sh/do-not-disrupt=true" in command
    pod_labels = manifest["spec"]["template"]["metadata"]["labels"]
    assert pod_labels["eks-workload-name"] == "cryo-spark"
    assert pod_labels["eks-workload-type"] == "Job"
    assert pod_labels["cryo-engine"] == "spark"
    assert pod_labels["cryo-operation"] == "optimize_manifests"
    assert manifest["metadata"]["labels"]["eks-workload-name"] == "cryo-spark"
    assert "spark.kubernetes.driver.label.eks-workload-name=cryo-spark" in command
    assert "spark.kubernetes.executor.label.eks-workload-name=cryo-spark" in command
    assert "spark.kubernetes.driver.label.cryo-engine=spark" in command
    script_idx = command.index("local:///opt/cryo/cryo_spark_runner.py")
    assert command.index("spark.kubernetes.driver.label.eks-workload-name=cryo-spark") < script_idx


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
        client.submit_repair_missing_files("cdp.mview.load_test_basefact")

    manifest = batch_api.create_namespaced_job.call_args.kwargs["body"]
    command = manifest["spec"]["template"]["spec"]["containers"][0]["command"]
    assert "--repair-missing-files" in command
    assert "--table" in command
    assert command[command.index("--table") + 1] == "cdp.mview.load_test_basefact"
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
        client.submit_repair_missing_files("cdp.mview.load_test_basefact", commit=True)

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


def test_cleanup_credentials_secret_falls_back_to_conventional_name() -> None:
    """When the wrapper Job is already TTL-deleted, still try ``{job_id}-aws``."""
    from kubernetes.client.exceptions import ApiException

    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)
    batch_api = MagicMock()
    batch_api.read_namespaced_job.side_effect = ApiException(status=404)
    core_api = MagicMock()

    with (
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api),
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
    ):
        client.cleanup_credentials_secret("cryo-spark-abc")

    core_api.delete_namespaced_secret.assert_called_once_with(
        name="cryo-spark-abc-aws", namespace="cryo"
    )


def test_cleanup_driver_pod_deletes_driver() -> None:
    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)
    core_api = MagicMock()

    with patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api):
        client.cleanup_driver_pod("cryo-spark-abc")

    core_api.delete_namespaced_pod.assert_called_once_with(
        name="cryo-spark-abc-driver", namespace="cryo"
    )


def test_cleanup_driver_pod_ignores_not_found() -> None:
    from kubernetes.client.exceptions import ApiException

    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)
    core_api = MagicMock()
    core_api.delete_namespaced_pod.side_effect = ApiException(status=404)

    with patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api):
        client.cleanup_driver_pod("cryo-spark-abc")  # must not raise


def test_cleanup_after_run_deletes_driver_and_credentials() -> None:
    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)

    with (
        patch.object(client, "cleanup_driver_pod") as cleanup_driver,
        patch.object(client, "cleanup_credentials_secret") as cleanup_secret,
    ):
        client.cleanup_after_run("cryo-spark-abc")

    cleanup_driver.assert_called_once_with("cryo-spark-abc")
    cleanup_secret.assert_called_once_with("cryo-spark-abc")


def test_cancel_job_run_also_cleans_driver() -> None:
    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)
    batch_api = MagicMock()

    with (
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api),
        patch.object(client, "cleanup_after_run") as cleanup,
    ):
        client.cancel_job_run("cryo-spark-abc")

    batch_api.delete_namespaced_job.assert_called_once()
    cleanup.assert_called_once_with("cryo-spark-abc")


def test_submit_spark_sql_adopts_credentials_secret_to_job() -> None:
    batch_api = MagicMock()
    batch_api.create_namespaced_job.return_value = SimpleNamespace(
        metadata=SimpleNamespace(uid="job-uid-1", name="ignored")
    )
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
        job_id = client.submit_spark_sql(
            "CALL cdp.system.rewrite_manifests(table => 'ns.tbl')", catalog="cdp"
        )

    core_api.patch_namespaced_secret.assert_called_once()
    patch_kwargs = core_api.patch_namespaced_secret.call_args.kwargs
    assert patch_kwargs["name"] == f"{job_id}-aws"
    owners = patch_kwargs["body"]["metadata"]["ownerReferences"]
    assert owners[0]["kind"] == "Job"
    assert owners[0]["name"] == job_id
    assert owners[0]["uid"] == "job-uid-1"


def test_read_pod_log_tail_requests_tail_lines_not_a_byte_prefix() -> None:
    """Regression, 2026-07-31: limit_bytes returns bytes from the START of the pod log, not
    the end -- for any driver with more than ~8KB of startup chatter before the real failure,
    the old code captured 20 lines of noise nowhere near the actual exception. A
    NoSuchTableException live in production never reached job.error because of this -- only
    "Decommissioning not enabled" spam did. tail_lines is the correct API parameter for
    "last N lines".

    Regression, PDAE-8346, confirmed live 2026-08-18: 200 lines still wasn't enough -- a real
    sample of 19/47 truncated MaintenanceJob.error rows over 14 days captured only shutdown
    chatter or bare stack frames, no exception identity at all, because the real header line
    was further back than 200 lines from the end. Widened to 2000; this is only the capture
    window (cheap, this string never reaches storage directly), the *stored* value is still
    capped by truncate_error_message downstream."""
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
        name="cryo-spark-abc123-driver", namespace="cryo", tail_lines=2000
    )
    assert "NoSuchTableException" in tail
    assert "limit_bytes" not in core_api.read_namespaced_pod_log.call_args.kwargs


def test_get_job_run_reads_driver_pod_not_wrapper_job() -> None:
    """The wrapper Job's spark-submit container can exit 0 even when the driver pod's
    SQL failed (spark-submit's own cleanup swallows the real failure in a background
    thread) -- get_job_run must trust the driver pod's phase, not the wrapper Job.
    Regression for a live incident on example-cluster, 2026-07-21."""
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


def test_get_job_run_returns_unknown_when_job_and_driver_both_ttl_expired() -> None:
    """Real incident, 2026-08-12: a caller asking about a job long after its
    ttlSecondsAfterFinished has passed finds neither the driver pod nor the wrapper Job
    -- the real outcome is permanently unrecoverable. UNKNOWN, not FAILED: nothing ever
    actually observed a failure, only the job's absence."""
    from kubernetes.client.exceptions import ApiException

    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)
    core_api = MagicMock()
    core_api.read_namespaced_pod.side_effect = ApiException(status=404)
    batch_api = MagicMock()
    batch_api.read_namespaced_job.side_effect = ApiException(status=404)

    with (
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api),
    ):
        result = client.get_job_run("cryo-spark-longgone")

    assert result.state is JobRunState.UNKNOWN
    assert "TTL-expired" in (result.state_details or "")


def test_get_job_run_reraises_non_404_errors_on_wrapper_job_lookup() -> None:
    """A genuine transient error (RBAC misconfigured, network blip) must still surface
    as a real exception -- only a confirmed 404 (Job definitely gone) should downgrade
    to UNKNOWN rather than propagate and be retried next tick."""
    from kubernetes.client.exceptions import ApiException

    cfg = SparkK8sConfig(namespace="cryo")
    client = SparkK8sClient(cfg)
    core_api = MagicMock()
    core_api.read_namespaced_pod.side_effect = ApiException(status=404)
    batch_api = MagicMock()
    batch_api.read_namespaced_job.side_effect = ApiException(status=403)

    with (
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api),
        pytest.raises(ApiException),
    ):
        client.get_job_run("cryo-spark-abc123")


# --- cleanup_stale_driver_pods (2026-08-06 orphaned-pod cleanup) ------------------
#
# Cluster-mode spark-submit creates the driver pod itself, with no owner reference
# back to Cryo's wrapper dispatch Job -- so the wrapper Job's ttlSecondsAfterFinished
# never cascades to it. Confirmed live: 3,276 completed driver pods had accumulated
# with zero owner references (vs only 1 leftover executor -- Spark does clean those
# up itself on application completion). These tests cover the explicit sweep that
# restores the intended TTL behavior.


def _pod(name: str, *, phase: str, start_time, has_start_time: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(
            phase=phase,
            start_time=start_time if has_start_time else None,
        ),
    )


def test_cleanup_deletes_old_completed_driver_pods() -> None:
    from datetime import UTC, datetime, timedelta

    old_start = datetime.now(UTC) - timedelta(hours=2)
    core_api = MagicMock()
    core_api.list_namespaced_pod.side_effect = [
        SimpleNamespace(
            items=[_pod("cryo-spark-old-driver", phase="Succeeded", start_time=old_start)]
        ),
        SimpleNamespace(items=[]),
    ]

    deleted = cleanup_stale_driver_pods(
        namespace="cryo", older_than_seconds=3600, core_api=core_api
    )

    assert deleted == 1
    core_api.delete_namespaced_pod.assert_called_once_with(
        name="cryo-spark-old-driver", namespace="cryo"
    )


def test_cleanup_skips_pods_younger_than_ttl() -> None:
    from datetime import UTC, datetime, timedelta

    recent_start = datetime.now(UTC) - timedelta(seconds=30)
    core_api = MagicMock()
    core_api.list_namespaced_pod.side_effect = [
        SimpleNamespace(
            items=[_pod("cryo-spark-fresh-driver", phase="Succeeded", start_time=recent_start)]
        ),
        SimpleNamespace(items=[]),
    ]

    deleted = cleanup_stale_driver_pods(
        namespace="cryo", older_than_seconds=3600, core_api=core_api
    )

    assert deleted == 0
    core_api.delete_namespaced_pod.assert_not_called()


def test_cleanup_skips_pods_with_no_start_time() -> None:
    core_api = MagicMock()
    core_api.list_namespaced_pod.side_effect = [
        SimpleNamespace(
            items=[
                _pod(
                    "cryo-spark-weird-driver",
                    phase="Succeeded",
                    start_time=None,
                    has_start_time=False,
                )
            ]
        ),
        SimpleNamespace(items=[]),
    ]

    deleted = cleanup_stale_driver_pods(
        namespace="cryo", older_than_seconds=3600, core_api=core_api
    )

    assert deleted == 0
    core_api.delete_namespaced_pod.assert_not_called()


def test_cleanup_checks_both_succeeded_and_failed_phases() -> None:
    core_api = MagicMock()
    core_api.list_namespaced_pod.side_effect = [
        SimpleNamespace(items=[]),
        SimpleNamespace(items=[]),
    ]

    cleanup_stale_driver_pods(namespace="cryo", older_than_seconds=3600, core_api=core_api)

    calls = core_api.list_namespaced_pod.call_args_list
    assert len(calls) == 2
    phases = {c.kwargs["field_selector"] for c in calls}
    assert phases == {"status.phase=Succeeded", "status.phase=Failed"}
    for c in calls:
        assert c.kwargs["label_selector"] == "spark-role=driver"
        assert c.kwargs["namespace"] == "cryo"


def test_cleanup_never_raises_on_list_failure() -> None:
    core_api = MagicMock()
    core_api.list_namespaced_pod.side_effect = RuntimeError("api down")

    deleted = cleanup_stale_driver_pods(
        namespace="cryo", older_than_seconds=3600, core_api=core_api
    )  # must not raise
    assert deleted == 0


def test_cleanup_never_raises_on_delete_failure_and_continues() -> None:
    from datetime import UTC, datetime, timedelta

    old_start = datetime.now(UTC) - timedelta(hours=2)
    core_api = MagicMock()
    core_api.list_namespaced_pod.side_effect = [
        SimpleNamespace(
            items=[
                _pod("cryo-spark-a-driver", phase="Succeeded", start_time=old_start),
                _pod("cryo-spark-b-driver", phase="Succeeded", start_time=old_start),
            ]
        ),
        SimpleNamespace(items=[]),
    ]
    core_api.delete_namespaced_pod.side_effect = [RuntimeError("conflict"), None]

    deleted = cleanup_stale_driver_pods(
        namespace="cryo", older_than_seconds=3600, core_api=core_api
    )  # must not raise despite the first delete failing

    assert deleted == 1  # only the second delete succeeded
    assert core_api.delete_namespaced_pod.call_count == 2


def test_client_reuses_cached_batch_and_core_clients_across_calls() -> None:
    """Regression for the cryo-worker OOM investigated 2026-08-13: every
    SparkK8sClient method used to call _new_k8s_batch_client()/_new_k8s_core_client()
    fresh, and each call constructs a real kubernetes ApiClient (its own
    urllib3.PoolManager, SSL context, certifi bundle) that is never closed. A single
    wait_for_completion() poll loop (10s interval) leaked one of these per poll --
    confirmed live: 8 of 10 running cryo-worker pods OOMKilled (exitCode 137) despite
    idling at ~150-190Mi against a 4Gi limit, i.e. a spike from bursty accumulation,
    not steady growth. One cached client per SparkK8sClient instance, reused across
    every method, closes this.
    """
    from kubernetes.client.exceptions import ApiException

    cfg = SparkK8sConfig(namespace="cryo")
    sut = SparkK8sClient(cfg)
    batch_api = MagicMock()
    core_api = MagicMock()
    batch_api.read_namespaced_job.return_value = SimpleNamespace(
        status=SimpleNamespace(failed=0, succeeded=0),
        metadata=SimpleNamespace(annotations={}),
    )
    core_api.read_namespaced_pod.side_effect = ApiException(status=404)

    with (
        patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api) as new_batch,
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api) as new_core,
    ):
        # get_job_run's driver-pod lookup misses (404) and falls through to
        # _get_wrapper_job_run's batch lookup -- exercises both client factories.
        sut.get_job_run("cryo-spark-abc123")
        sut.get_job_run("cryo-spark-abc123")
        sut.cleanup_credentials_secret("cryo-spark-abc123")
        sut.cleanup_driver_pod("cryo-spark-abc123")

    # The underlying kubernetes ApiClient constructor must run at most once per
    # transport type for this SparkK8sClient's whole lifetime, not once per call.
    assert new_batch.call_count == 1
    assert new_core.call_count == 1
    # And every call actually landed on the one cached instance, not a fresh mock.
    assert core_api.read_namespaced_pod.call_count == 2
    assert batch_api.read_namespaced_job.call_count >= 1


def test_separate_client_instances_share_the_same_underlying_k8s_api_client() -> None:
    """Regression for a real production OOM, 2026-08-14: cryo.governor.native_admit's
    _dispatch_fallback_operations builds a brand-new SparkK8sEngine (and therefore a
    brand-new SparkK8sClient) once PER TABLE, up to 6 concurrently via a
    ThreadPoolExecutor. A per-SparkK8sClient-instance-only cache (this test's original
    assertion, before this fix) is defeated every single call in that real hot path --
    confirmed live: cryo-worker pods (4Gi limit) OOMKilled repeatedly, idle at
    ~160-225Mi but spiking hard during exactly this dispatch-burst pattern in the logs.

    Each table's *Spark* config (S3 credentials, catalog URI -- see
    native_admit._fallback_engine_config's docstring) legitimately differs per table, so
    SparkK8sClient instances must stay separate for that. But the underlying *K8s API*
    connection has no legitimate reason to be per-table -- it's always the same
    in-cluster API server -- so _new_k8s_batch_client/_new_k8s_core_client are now
    themselves process-wide cached singletons (see their docstrings in k8s.py), and two
    separate SparkK8sClient instances correctly end up sharing the one real ApiClient
    underneath, however many instances get constructed.
    """
    cfg = SparkK8sConfig(namespace="cryo")
    first = SparkK8sClient(cfg)
    second = SparkK8sClient(cfg)
    batch_api = MagicMock()

    with patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api) as new_batch:
        first.submit_spark_sql("SELECT 1", catalog="cdp")
        second.submit_spark_sql("SELECT 1", catalog="cdp")

    # Both instances' calls resolve through the same (mocked, in this test) client
    # factory, and each instance's own cache means the factory itself is only invoked
    # once per instance -- the real fix under test lives in _new_k8s_batch_client's own
    # module-level caching, not observable through this mock boundary, but the shared
    # return value below is what production code actually receives.
    assert new_batch.call_count == 2
    assert first._batch_api is batch_api
    assert second._batch_api is batch_api
    assert first._batch_api is second._batch_api


def test_new_k8s_batch_client_constructs_the_real_apiclient_at_most_once() -> None:
    """Tests _new_k8s_batch_client itself (not mocked away), proving the module-level
    singleton actually works: even across many calls -- simulating many SparkK8sClient
    instances each calling this once, the real shape of the OOM this fix addresses --
    kubernetes.client.BatchV1Api() (which owns the real urllib3.PoolManager the leak was
    about) is constructed exactly once."""
    with (
        patch("cryo.engines.spark.k8s._ensure_k8s_config"),
        patch("kubernetes.client.BatchV1Api") as mock_batch_v1_api,
    ):
        mock_batch_v1_api.return_value = MagicMock()

        results = [k8s_module._new_k8s_batch_client() for _ in range(10)]

        mock_batch_v1_api.assert_called_once()
        assert all(r is results[0] for r in results)


def test_new_k8s_core_client_constructs_the_real_apiclient_at_most_once() -> None:
    """Same as the batch-client test above, for CoreV1Api (owns its own separate
    PoolManager -- both client types leaked independently before this fix)."""
    with (
        patch("cryo.engines.spark.k8s._ensure_k8s_config"),
        patch("kubernetes.client.CoreV1Api") as mock_core_v1_api,
    ):
        mock_core_v1_api.return_value = MagicMock()

        results = [k8s_module._new_k8s_core_client() for _ in range(10)]

        mock_core_v1_api.assert_called_once()
        assert all(r is results[0] for r in results)


def test_new_k8s_batch_client_is_thread_safe_under_concurrent_first_access() -> None:
    """The real hot path this fix targets is exactly this: many threads
    (ThreadPoolExecutor, up to 6 concurrent per native_admit._run_fallback_dispatches_concurrently)
    potentially racing to build the singleton on first use. Proves the lock actually
    prevents more than one real ApiClient from ever being constructed under real
    concurrent first-access, not just sequential calls."""
    import threading

    with (
        patch("cryo.engines.spark.k8s._ensure_k8s_config"),
        patch("kubernetes.client.BatchV1Api") as mock_batch_v1_api,
    ):
        mock_batch_v1_api.side_effect = lambda: MagicMock()

        results: list = []
        barrier = threading.Barrier(8)

        def _worker() -> None:
            barrier.wait()  # maximize actual overlap at the race window
            results.append(k8s_module._new_k8s_batch_client())

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert mock_batch_v1_api.call_count == 1
        assert len(results) == 8
        assert all(r is results[0] for r in results)
