from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cryo.domain.enums import Mode, OperationType, PartitionTransform
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    PartitionPredicate,
    PartitionPredicateTerm,
)
from cryo.engines.spark import worker_guard as wg
from cryo.engines.spark.k8s import JobRunResult, JobRunState
from cryo.engines.spark.k8s_engine import SparkK8sEngine
from cryo.engines.spark.sql import render


def _predicate(i: int) -> PartitionPredicate:
    return PartitionPredicate(
        terms=(
            PartitionPredicateTerm(
                base_column="dt",
                transform=PartitionTransform.IDENTITY,
                value=str(i),
            ),
        )
    )


def _optimize_plan(*, n_predicates: int) -> MaintenancePlan:
    return _chunked_optimize_plan((n_predicates,))


def _chunked_optimize_plan(predicate_counts: tuple[int, ...]) -> MaintenancePlan:
    next_predicate = 0
    operations: list[Operation] = []
    for predicate_count in predicate_counts:
        predicates = tuple(
            _predicate(predicate_index)
            for predicate_index in range(next_predicate, next_predicate + predicate_count)
        )
        operations.append(Operation(OperationType.OPTIMIZE, {}, predicates=predicates))
        next_predicate += predicate_count
    return MaintenancePlan(
        table_ref="cdp.pw_bq.silver_dbt_widget_clicked",
        mode=Mode.DEEP,
        operations=tuple(operations),
    )


def test_process_rss_mib_is_bounded_on_this_platform() -> None:
    result = wg.process_rss_mib()
    assert result is not None
    assert 0 < result < 10_000


def test_small_plan_has_no_outlier_telemetry() -> None:
    plan = _optimize_plan(n_predicates=3)
    snap = wg.footprint(
        plan=plan,
        stats={"total_size_gb": 12.0, "total_files": 400},
        partitions=[{"partition_value": "a"}],
    )
    assert snap.outlier_reason is None
    assert snap.predicate_count == 3


def test_large_plan_preserves_partition_filters() -> None:
    plan = _optimize_plan(n_predicates=80)
    snap = wg.footprint(
        plan=plan,
        stats={"total_size_gb": 1970.0, "total_files": 30_547},
        partitions=[{"partition_value": i} for i in range(80)],
    )
    assert snap.outlier_reason is not None
    assert "total_size_gb" in snap.outlier_reason
    assert plan.operations[0].predicates is not None
    sql = render(plan)[0].sql
    assert "where =>" in sql
    assert "rewrite_data_files" in sql


def test_predicate_count_is_observed_without_mutating_scope() -> None:
    plan = _optimize_plan(n_predicates=wg.OUTLIER_PREDICATE_COUNT)
    snap = wg.footprint(plan=plan, stats={"total_size_gb": 1.0})
    assert snap.outlier_reason is not None
    assert plan.operations[0].predicates is not None


def test_execute_plan_keeps_filters_for_high_predicate_count() -> None:
    engine = SparkK8sEngine(config={"catalog": "cdp"})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.return_value = "cryo-spark-oom1"
    mock_client.wait_for_completion.return_value = JobRunResult(
        job_run_id="cryo-spark-oom1", state=JobRunState.SUCCESS
    )
    engine._client = mock_client

    results = engine.execute_plan(_chunked_optimize_plan((128, 128)))
    assert len(results) == 2
    assert all(result.ok for result in results)
    sql_statements = [call.args[0] for call in mock_client.submit_spark_sql.call_args_list]
    assert len(sql_statements) == 2
    assert all("where =>" in sql for sql in sql_statements)


def test_execute_plan_keeps_filters_for_small_tables() -> None:
    engine = SparkK8sEngine(config={"catalog": "cdp"})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.return_value = "cryo-spark-small"
    mock_client.wait_for_completion.return_value = JobRunResult(
        job_run_id="cryo-spark-small", state=JobRunState.SUCCESS
    )
    engine._client = mock_client

    results = engine.execute_plan(_optimize_plan(n_predicates=2))
    assert results[0].ok
    sql = mock_client.submit_spark_sql.call_args.args[0]
    assert "where =>" in sql


def test_validate_spark_worker_plan_preserves_scoped_outlier() -> None:
    from cryo.worker.worker import (
        TableMaintenanceData,
        _validate_spark_worker_plan,
    )

    table_data = TableMaintenanceData(
        stats={"total_size_gb": 1970.0, "total_files": 30_547},
        partitions=[{"partition_value": str(i)} for i in range(40)],
    )
    plan = _optimize_plan(n_predicates=40)
    _validate_spark_worker_plan(
        plan,
        table_data=table_data,
        table_ref=None,
    )
    assert plan.operations[0].predicates is not None


def test_validate_spark_plan_rejects_out_of_budget_full_table_rewrite() -> None:
    plan = MaintenancePlan(
        table_ref="cdp.pw_bq.silver_dbt_widget_clicked",
        mode=Mode.DEEP,
        operations=(Operation(OperationType.OPTIMIZE, {"chunk_size_gb": 50}),),
    )

    with pytest.raises(wg.SparkPlanScopeError, match="outside chunk size budget"):
        wg.validate_spark_plan_scope(plan, stats={"total_size_gb": 50.1})


def test_execute_plan_rejects_unscoped_optimize_without_scan_size_evidence() -> None:
    engine = SparkK8sEngine(config={"catalog": "cdp"})
    mock_client = MagicMock()
    engine._client = mock_client
    plan = MaintenancePlan(
        table_ref="cdp.pw_bq.silver_dbt_widget_clicked",
        mode=Mode.DEEP,
        operations=(Operation(OperationType.OPTIMIZE, {"chunk_size_gb": 50}),),
    )

    with pytest.raises(wg.SparkPlanScopeError, match="without scan size evidence"):
        engine.execute_plan(plan)
    mock_client.submit_spark_sql.assert_not_called()


def test_execute_plan_rejects_out_of_budget_unscoped_optimize_with_table_stats() -> None:
    engine = SparkK8sEngine(config={"catalog": "cdp"}).with_table_stats({"total_size_gb": 50.1})
    mock_client = MagicMock()
    engine._client = mock_client
    plan = MaintenancePlan(
        table_ref="cdp.pw_bq.silver_dbt_widget_clicked",
        mode=Mode.DEEP,
        operations=(Operation(OperationType.OPTIMIZE, {"chunk_size_gb": 50}),),
    )

    with pytest.raises(wg.SparkPlanScopeError, match="outside chunk size budget"):
        engine.execute_plan(plan)
    mock_client.submit_spark_sql.assert_not_called()


@pytest.mark.parametrize("predicates", [None, ()])
def test_execute_plan_rejects_duplicate_unscoped_optimize_calls(
    predicates: tuple[PartitionPredicate, ...] | None,
) -> None:
    engine = SparkK8sEngine(config={"catalog": "cdp"})
    mock_client = MagicMock()
    engine._client = mock_client
    plan = MaintenancePlan(
        table_ref="cdp.pw_bq.silver_dbt_widget_clicked",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.OPTIMIZE, {}, predicates=predicates),
            Operation(OperationType.OPTIMIZE, {}, predicates=predicates),
        ),
    )

    with pytest.raises(wg.SparkPlanScopeError, match="multiple unscoped"):
        engine.execute_plan(plan)
    mock_client.submit_spark_sql.assert_not_called()
