"""Unit tests for SparkK8sEngine (MaintenanceEngine over Cryo-managed Spark-on-K8s)."""

from __future__ import annotations

from unittest.mock import MagicMock

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.spark.k8s import JobRunResult, JobRunState
from cryo.engines.spark.k8s_engine import SparkK8sEngine


def _plan() -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="cdp.mview.load_test_basefact",
        mode=Mode.DEEP,
        operations=(Operation(OperationType.OPTIMIZE_MANIFESTS, {}),),
    )


def test_render_produces_spark_sql() -> None:
    engine = SparkK8sEngine(config={})
    statements = engine.render(_plan())
    assert len(statements) == 1
    assert "rewrite_manifests" in statements[0].sql


def test_execute_success_returns_ok_result() -> None:
    engine = SparkK8sEngine(config={"catalog": "cdp"})
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
    assert mock_client.submit_spark_sql.call_args.kwargs["catalog"] == "cdp"
    mock_client.cleanup_after_run.assert_called_once_with("cryo-spark-abc123")


def test_execute_cleans_up_driver_even_on_failure() -> None:
    engine = SparkK8sEngine(config={"catalog": "cdp"})
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
    to a destination statically configured for "cdp" -- Spark registered its Iceberg catalog
    plugin under "cdp" while the rendered SQL referenced "hive.system.rewrite_data_files",
    silently resolving against the builtin spark_catalog and failing with
    MISSING_CATALOG_ABILITY.PROCEDURES. The catalog actually used to render the SQL (carried
    on the statement's own metadata) must win over the destination's static default."""
    engine = SparkK8sEngine(config={"catalog": "cdp"})
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


def test_execute_submission_exception_is_caught() -> None:
    engine = SparkK8sEngine(config={})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.side_effect = RuntimeError("k8s api unavailable")
    engine._client = mock_client

    results = engine.execute_plan(_plan())
    assert len(results) == 1
    assert not results[0].ok
    assert "k8s api unavailable" in results[0].error


def test_probe_capacity_uses_slot_cap() -> None:
    engine = SparkK8sEngine(config={"slot_cap": 7})
    snapshot = engine.probe_capacity()
    assert snapshot.capacity == 7
    assert snapshot.used == 0
