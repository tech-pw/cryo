from __future__ import annotations

from unittest.mock import MagicMock

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
    return MaintenancePlan(
        table_ref="cdp.pw_bq.silver_dbt_widget_clicked",
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {},
                predicates=tuple(_predicate(i) for i in range(n_predicates)),
            ),
        ),
    )


def test_process_rss_mib_is_bounded_on_this_platform() -> None:
    result = wg.process_rss_mib()
    assert result is not None
    assert 0 < result < 10_000


def test_small_plan_is_not_compacted() -> None:
    plan = _optimize_plan(n_predicates=3)
    compacted, snap = wg.maybe_compact_spark_plan(
        plan,
        stats={"total_size_gb": 12.0, "total_files": 400},
        partitions=[{"partition_value": "a"}],
    )
    assert compacted is plan
    assert snap.outlier_reason is None
    assert snap.predicate_count == 3


def test_lead_table_stats_compact_to_full_table_call() -> None:
    """PDAE-8348 lead: ~1.97TB / 30,547 files must not keep thousands of predicates
    on the in-process worker."""
    plan = _optimize_plan(n_predicates=80)
    compacted, snap = wg.maybe_compact_spark_plan(
        plan,
        stats={"total_size_gb": 1970.0, "total_files": 30_547},
        partitions=[{"partition_value": i} for i in range(80)],
    )
    assert snap.outlier_reason is not None
    assert "total_size_gb" in snap.outlier_reason
    assert compacted is not plan
    assert compacted.operations[0].predicates is None
    sql = render(compacted)[0].sql
    assert "where =>" not in sql
    assert "rewrite_data_files" in sql


def test_predicate_count_alone_triggers_compact() -> None:
    plan = _optimize_plan(n_predicates=wg.OUTLIER_PREDICATE_COUNT)
    compacted, snap = wg.maybe_compact_spark_plan(plan, stats={"total_size_gb": 1.0})
    assert snap.outlier_reason is not None
    assert compacted.operations[0].predicates is None


def test_execute_plan_compacts_outlier_before_submit() -> None:
    engine = SparkK8sEngine(config={"catalog": "cdp"})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.return_value = "cryo-spark-oom1"
    mock_client.wait_for_completion.return_value = JobRunResult(
        job_run_id="cryo-spark-oom1", state=JobRunState.SUCCESS
    )
    engine._client = mock_client

    results = engine.execute_plan(_optimize_plan(n_predicates=wg.OUTLIER_PREDICATE_COUNT))
    assert results[0].ok
    sql = mock_client.submit_spark_sql.call_args.args[0]
    assert "where =>" not in sql


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


def test_maybe_compact_spark_worker_plan_persists_compacted_plan() -> None:
    from cryo.domain.enums import JobStatus
    from cryo.worker.worker import (
        InMemoryJobStore,
        MaintenanceJobRecord,
        TableMaintenanceData,
        _maybe_compact_spark_worker_plan,
    )

    store = InMemoryJobStore()
    job = MaintenanceJobRecord(
        id="job-1",
        run_id="run-1",
        table_id="table-1",
        destination_id="spark-dest",
        mode=Mode.DEEP,
        status=JobStatus.RUNNING,
    )
    store.add_job(job)
    table_data = TableMaintenanceData(
        stats={"total_size_gb": 1970.0, "total_files": 30_547},
        partitions=[{"partition_value": str(i)} for i in range(40)],
    )
    compacted = _maybe_compact_spark_worker_plan(
        _optimize_plan(n_predicates=40),
        job=job,
        table_data=table_data,
        table_ref="cdp.pw_bq.silver_dbt_widget_clicked",
        store=store,
    )
    assert compacted.operations[0].predicates is None
    assert store.jobs["job-1"].plan is not None
    assert any("compacted partition filters" in n for n in store.jobs["job-1"].plan["notes"])
