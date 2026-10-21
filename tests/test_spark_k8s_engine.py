"""Unit tests for SparkK8sEngine (MaintenanceEngine over Cryo-managed Spark-on-K8s)."""

from __future__ import annotations

from unittest.mock import MagicMock

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.spark.k8s import JobRunResult, JobRunState
from cryo.engines.spark.k8s_engine import SparkK8sEngine, is_spark_retryable_catalog_error


def _plan() -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="demo.mart.load_test_events",
        mode=Mode.DEEP,
        operations=(Operation(OperationType.OPTIMIZE_MANIFESTS, {}),),
    )


def test_render_produces_spark_sql() -> None:
    engine = SparkK8sEngine(config={})
    statements = engine.render(_plan())
    assert len(statements) == 1
    assert "rewrite_manifests" in statements[0].sql


def test_execute_success_returns_ok_result() -> None:
    engine = SparkK8sEngine(config={"catalog": "sample"})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.return_value = "cryo-spark-abc123"
    mock_client.wait_for_completion.return_value = JobRunResult(
        job_run_id="cryo-spark-abc123", state=JobRunState.SUCCESS
    )
    engine._client = mock_client

    results = engine.execute_plan(_plan())
    assert len(results) == 1
    assert results[0].ok
    assert results[0].engine_ref == "cryo-spark-abc123"
    mock_client.submit_spark_sql.assert_called_once()
    # The rendered statement's own metadata["catalog"] (derived from the plan's table_ref,
    # "demo.mart.load_test_events") wins over the engine's configured catalog ("sample") --
    # see SparkK8sEngine.submit: `stmt.metadata.get("catalog") or self._catalog`.
    assert mock_client.submit_spark_sql.call_args.kwargs["catalog"] == "demo"
    mock_client.cleanup_after_run.assert_called_once_with("cryo-spark-abc123")


def test_execute_cleans_up_driver_even_on_failure() -> None:
    engine = SparkK8sEngine(config={"catalog": "sample"})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.return_value = "cryo-spark-fail1"
    mock_client.wait_for_completion.return_value = JobRunResult(
        job_run_id="cryo-spark-fail1",
        state=JobRunState.FAILED,
        state_details="boom",
    )
    engine._client = mock_client

    results = engine.execute_plan(_plan())
    assert not results[0].ok
    mock_client.cleanup_after_run.assert_called_once_with("cryo-spark-fail1")


def test_execute_uses_the_statements_own_catalog_over_the_destination_default() -> None:
    """Regression, 2026-07-24: a shared SQL-fallback destination's `catalog` config is only
    a static default (correct for whichever table it was tuned for), but execute() used to
    trust it unconditionally. Confirmed live: a table whose real catalog is "hive" fell back
    to a destination statically configured for "sample" -- Spark registered its Iceberg catalog
    plugin under "sample" while the rendered SQL referenced "hive.system.rewrite_data_files",
    silently resolving against the builtin spark_catalog and failing with
    MISSING_CATALOG_ABILITY.PROCEDURES. The catalog actually used to render the SQL (carried
    on the statement's own metadata) must win over the destination's static default."""
    engine = SparkK8sEngine(config={"catalog": "sample"})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.return_value = "cryo-spark-abc123"
    mock_client.wait_for_completion.return_value = JobRunResult(
        job_run_id="cryo-spark-abc123", state=JobRunState.SUCCESS
    )
    engine._client = mock_client

    hive_plan = MaintenancePlan(
        table_ref="hive.lms.silver_adminactions",
        mode=Mode.DEEP,
        operations=(Operation(OperationType.OPTIMIZE_MANIFESTS, {}),),
    )
    results = engine.execute_plan(hive_plan)
    assert len(results) == 1
    assert results[0].ok
    assert mock_client.submit_spark_sql.call_args.kwargs["catalog"] == "hive"


def test_execute_retries_basemetastore_nosuchkey_then_succeeds(monkeypatch) -> None:
    """: after deepest_caused_by, gold-table Spark failures are NoSuchKey while
    loading BaseMetastoreTableOperations.current -- a stale catalog pointer, not a drop.
    Retry the K8s Job instead of failing the first driver."""
    sleeps: list[float] = []
    monkeypatch.setattr("cryo.engines.spark.k8s_engine.time.sleep", sleeps.append)

    engine = SparkK8sEngine(config={"catalog": "sample"})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.side_effect = ["cryo-spark-try0", "cryo-spark-try1"]
    mock_client.wait_for_completion.side_effect = [
        JobRunResult(
            job_run_id="cryo-spark-try0",
            state=JobRunState.FAILED,
            state_details=(
                "at org.apache.iceberg.BaseMetastoreTableOperations.current("
                "BaseMetastoreTableOperations.java:72)\n"
                "Caused by: software.amazon.awssdk.services.s3.model.NoSuchKeyException: "
                "The specified key does not exist."
            ),
        ),
        JobRunResult(job_run_id="cryo-spark-try1", state=JobRunState.SUCCESS),
    ]
    engine._client = mock_client

    results = engine.execute_plan(_plan())
    assert results[0].ok
    assert mock_client.submit_spark_sql.call_count == 2
    assert sleeps == [10]
    mock_client.cleanup_after_run.assert_any_call("cryo-spark-try0")
    mock_client.cleanup_after_run.assert_any_call("cryo-spark-try1")


def test_execute_does_not_retry_dropped_table() -> None:
    engine = SparkK8sEngine(config={})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.return_value = "cryo-spark-drop"
    mock_client.wait_for_completion.return_value = JobRunResult(
        job_run_id="cryo-spark-drop",
        state=JobRunState.FAILED,
        state_details="Caused by: org.apache.spark.sql.catalyst.analysis.NoSuchTableException",
    )
    engine._client = mock_client

    results = engine.execute_plan(_plan())
    assert not results[0].ok
    mock_client.submit_spark_sql.assert_called_once()


def test_is_spark_retryable_catalog_error() -> None:
    assert is_spark_retryable_catalog_error(
        "at org.apache.iceberg.BaseMetastoreTableOperations.current\nNoSuchKeyException"
    )
    assert is_spark_retryable_catalog_error("Caused by: CommitFailedException: conflict")
    assert not is_spark_retryable_catalog_error("NoSuchTableException: gone")
    assert not is_spark_retryable_catalog_error("OutOfMemoryError: Java heap space")


def test_execute_failure_returns_error_result() -> None:
    engine = SparkK8sEngine(config={})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.return_value = "cryo-spark-def456"
    mock_client.wait_for_completion.return_value = JobRunResult(
        job_run_id="cryo-spark-def456",
        state=JobRunState.FAILED,
        state_details="rewrite_manifests failed: boom",
    )
    engine._client = mock_client

    results = engine.execute_plan(_plan())
    assert len(results) == 1
    assert not results[0].ok
    assert "boom" in results[0].error


def test_execute_failure_preserves_table_not_found_signature_past_truncation() -> None:
    """Regression, 2026-07-31: a driver's raw log tail is a full stack trace that can run past
    15KB, with "Caused by: NoSuchTableException" often well past the ~4000-char head+tail
    window truncate_error_message keeps -- confirmed live, the signature was in the untruncated
    log but never reached the stored error, so is_table_not_found_error() downstream never
    fired. The signature must survive even when it originally sat past both the head and tail
    cutoffs."""
    from cryo.engines.base import is_table_not_found_error

    padding = "INFO stack frame noise line\n" * 200
    state_details = (
        padding + "Caused by: org.apache.spark.sql.catalyst.analysis.NoSuchTableException: "
        "[TABLE_OR_VIEW_NOT_FOUND] not found\n" + padding
    )
    assert not is_table_not_found_error(state_details[:3500])
    assert not is_table_not_found_error(state_details[-500:])

    engine = SparkK8sEngine(config={})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.return_value = "cryo-spark-ghi789"
    mock_client.wait_for_completion.return_value = JobRunResult(
        job_run_id="cryo-spark-ghi789",
        state=JobRunState.FAILED,
        state_details=state_details,
    )
    engine._client = mock_client

    results = engine.execute_plan(_plan())
    assert len(results) == 1
    assert not results[0].ok
    assert len(results[0].error) < 4200
    assert is_table_not_found_error(results[0].error)


def test_execute_submission_exception_is_caught() -> None:
    engine = SparkK8sEngine(config={})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.side_effect = RuntimeError("k8s api unavailable")
    engine._client = mock_client

    results = engine.execute_plan(_plan())
    assert len(results) == 1
    assert not results[0].ok
    assert "k8s api unavailable" in results[0].error


def test_execute_truncates_runaway_error_messages() -> None:
    """Regression, 2026-07-27: no engine's except-block may store an unbounded exception
    message -- confirmed live on Trino (str(exc) landed a 2.96M-character error). Same
    truncate_error_message helper applies here for consistency."""
    engine = SparkK8sEngine(config={"catalog": "sample"})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.side_effect = RuntimeError("x" * 20_000)
    engine._client = mock_client

    results = engine.execute_plan(_plan())
    assert len(results) == 1
    assert not results[0].ok
    assert len(results[0].error) < 4200
    assert "truncated" in results[0].error


def test_with_source_config_fills_in_missing_s3_credentials() -> None:
    """Regression, 2026-07-28: the native engine has inherited source-configured S3 keys
    since day one (cryo/engines/native/payload.py), and the worker dispatch loop already
    calls engine.with_source_config(...) generically on any engine defining it -- Spark-on-K8s
    just never implemented the hook. Confirmed live: a table whose source had static
    s3.access-key-id/s3.secret-access-key configured (the operator's deliberate "keys, not
    IAM role" choice) still fell through to the pod's ambient node IAM role for Spark-K8s
    jobs, which lacked s3:GetObject on that table's bucket."""
    engine = SparkK8sEngine(config={"catalog": "sample"})
    assert engine._k8s_config.s3_access_key is None

    updated = engine.with_source_config(
        {"s3.access-key-id": "AKIAEXAMPLE", "s3.secret-access-key": "secret123"}
    )
    assert updated is not engine
    assert updated._k8s_config.s3_access_key == "AKIAEXAMPLE"
    assert updated._k8s_config.s3_secret_key == "secret123"
    # Original engine instance is untouched.
    assert engine._k8s_config.s3_access_key is None


def test_with_source_config_does_not_override_destinations_own_keys() -> None:
    """An explicit s3_access_key/s3_secret_key already set on the destination is an operator
    override (e.g. a maintenance-specific write role) and must win over the table's
    source-configured read credentials."""
    engine = SparkK8sEngine(
        config={
            "catalog": "sample",
            "spark_k8s": {"s3_access_key": "dest-key", "s3_secret_key": "dest-secret"},
        }
    )
    updated = engine.with_source_config(
        {"s3.access-key-id": "AKIAEXAMPLE", "s3.secret-access-key": "secret123"}
    )
    assert updated is engine
    assert updated._k8s_config.s3_access_key == "dest-key"
    assert updated._k8s_config.s3_secret_key == "dest-secret"


def test_with_source_config_no_op_when_source_has_nothing_to_fill() -> None:
    engine = SparkK8sEngine(config={"catalog": "sample"})
    updated = engine.with_source_config({})
    assert updated is engine


def test_with_source_config_fills_catalog_uri_from_the_source() -> None:
    """A REST source with a URI and no static S3 keys still needs the URI on the submit.

    Without --catalog-uri the runner never sets spark.sql.catalog.<name>.uri, Spark falls
    back to spark_catalog, and the driver dies with "Invalid uri for http client: null"
    (issue #143, 2026-10-21).
    """
    engine = SparkK8sEngine(config={"catalog": "bench", "spark_k8s": {"catalog_impl": "rest"}})
    updated = engine.with_source_config(
        {"uri": "http://iceberg-rest:8181", "warehouse": "s3://bench/warehouse"}
    )
    assert updated is not engine
    assert updated._k8s_config.catalog_uri == "http://iceberg-rest:8181"
    assert updated._k8s_config.catalog_warehouse == "s3://bench/warehouse"

    destination_wins = SparkK8sEngine(
        config={
            "catalog": "bench",
            "spark_k8s": {"catalog_uri": "http://destination-rest:8181"},
        }
    )
    kept = destination_wins.with_source_config({"uri": "http://source-rest:8181"})
    assert kept._k8s_config.catalog_uri == "http://destination-rest:8181"


def test_with_table_stats_scales_executor_profile_down_for_small_tables() -> None:
    """Regression, 2026-08-03: every job on a shared Spark-K8s destination got the same
    static executor profile regardless of table size -- a table with a dozen files reserved
    the same 76 vCPU / 160 GiB as a concurrently-running 24TB table. Confirmed live via
    Grafana: 86% of the cryo namespace's cost was "unused" (reserved, not utilized) for
    exactly this reason."""
    engine = SparkK8sEngine(
        config={
            "catalog": "sample",
            "spark_k8s": {
                "executor_instances": 24,
                "executor_cpu_milli": 3000,
                "executor_mem_mib": 6144,
                "driver_cpu_milli": 4000,
                "driver_mem_mib": 16384,
            },
        }
    )
    updated = engine.with_table_stats({"total_size_gb": 0.2})
    assert updated is not engine
    assert updated._k8s_config.executor_instances < 24
    assert updated._k8s_config.executor_cpu_milli < 3000
    # Original engine instance (and its config) is untouched.
    assert engine._k8s_config.executor_instances == 24


def test_with_table_stats_keeps_full_profile_for_a_huge_table() -> None:
    engine = SparkK8sEngine(config={"catalog": "sample", "spark_k8s": {"executor_instances": 24}})
    updated = engine.with_table_stats({"total_size_gb": 24_000.0})
    assert updated._k8s_config.executor_instances == 24


def test_with_table_stats_no_op_without_stats() -> None:
    engine = SparkK8sEngine(config={"catalog": "sample", "spark_k8s": {"executor_instances": 24}})
    assert engine.with_table_stats(None) is engine


def test_with_table_stats_respects_dynamic_sizing_opt_out() -> None:
    engine = SparkK8sEngine(
        config={
            "catalog": "sample",
            "spark_k8s": {"executor_instances": 24, "dynamic_sizing": False},
        }
    )
    updated = engine.with_table_stats({"total_size_gb": 0.2})
    assert updated._k8s_config.executor_instances == 24


def test_with_table_stats_preserves_prior_with_source_config_state() -> None:
    """with_source_config runs before with_table_stats in the real dispatch path (see
    worker.py) -- chaining the two must not lose the first hook's changes."""
    engine = SparkK8sEngine(config={"catalog": "sample", "spark_k8s": {"executor_instances": 24}})
    with_creds = engine.with_source_config(
        {"s3.access-key-id": "AKIAEXAMPLE", "s3.secret-access-key": "secret123"}
    )
    final = with_creds.with_table_stats({"total_size_gb": 0.2})
    assert final._k8s_config.s3_access_key == "AKIAEXAMPLE"
    assert final._k8s_config.executor_instances < 24


def test_probe_capacity_uses_slot_cap() -> None:
    engine = SparkK8sEngine(config={"slot_cap": 7})
    snapshot = engine.probe_capacity()
    assert snapshot.capacity == 7
    assert snapshot.used == 0
