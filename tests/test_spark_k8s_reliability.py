"""Spark-on-Kubernetes reliability: executor OOM (#166), quota refusals (#159), sizing-key
validation (#160) and rewrite metrics (#155)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cryo.api.schemas import DestinationCreate, validate_destination_config
from cryo.domain.enums import EngineType, JobStatus, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.base import (
    CAPACITY_REFUSED_MARKER,
    capacity_refusal_error,
    is_capacity_refusal,
    truncate_error_message,
)
from cryo.engines.spark.k8s import (
    JobRunResult,
    JobRunState,
    SparkK8sClient,
    SparkK8sConfig,
    _capped_submit_config,
    executor_oom_summary,
)
from cryo.engines.spark.k8s_engine import SparkK8sEngine, is_spark_executor_oom
from cryo.engines.spark.procedure_metrics import result_metrics_from_log, rewrite_file_counts
from cryo.engines.spark.sizing import executor_heap_after_oom

_NPE = (
    'Caused by: java.lang.NullPointerException: Cannot invoke "org.apache.spark.sql.classic.'
    'SparkSession.sparkContext()" because the return value of "org.apache.spark.sql.execution.'
    'SparkPlan.session()" is null'
)
_EXECUTOR_OOM_LOG = "\n".join(
    [
        "INFO DAGScheduler: Shuffle files lost for executor: 2",
        "ERROR TaskSchedulerImpl: Lost executor 2 on 10.0.0.7: The executor with id 2 exited "
        "with exit code 137(SIGKILL, possible container OOM).",
        "The API gave the following container statuses:",
        "\t container name: spark-kubernetes-executor",
        "\t container image: ghcr.io/tech-pw/cryo-spark:latest",
        "\t container state: terminated",
        "\t exit code: 137",
        "\t termination reason: OOMKilled",
        *(["INFO TaskSetManager: Finished task 265.0 in stage 1.0"] * 200),
        "pyspark.errors.exceptions.captured.Py4JJavaError: An error occurred",
        _NPE,
    ]
)
_QUOTA_LOG = (
    "Exception in thread main io.fabric8.kubernetes.client.KubernetesClientException: Failure "
    "executing: POST at: https://kubernetes.default.svc/api/v1/namespaces/cryo/pods. Message: pods "
    '"cryo-spark-abc-driver" is forbidden: exceeded quota: compute-quota, requested: '
    "limits.cpu=1, used: limits.cpu=47800m, limited: limits.cpu=48. code=403, reason=Forbidden"
)


def _optimize_plan() -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="cdp.mart.events",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, {"file_size_mb": 256}),),
    )


def _engine(stats: dict[str, object] | None = None) -> tuple[SparkK8sEngine, MagicMock]:
    engine = SparkK8sEngine(config={"catalog": "cdp"})
    if stats is not None:
        engine = engine.with_table_stats(stats)
    client = MagicMock()
    engine._client = client
    return engine, client


# --- #166 executor OOM ----------------------------------------------------------------------


def test_executor_oom_summary_reads_the_executor_kill_not_the_npe() -> None:
    assert executor_oom_summary(_EXECUTOR_OOM_LOG) == "Executor OOMKilled exit 137 (executor 2)"
    # Exit 137 without the kubelet's OOMKilled reason is eviction/preemption too.
    assert executor_oom_summary("The executor with id 2 exited with exit code 137") is None
    # The driver container's own OOMKilled is the driver path, not this one.
    assert (
        executor_oom_summary(
            "container name: spark-kubernetes-driver\ntermination reason: OOMKilled"
        )
        is None
    )
    assert executor_oom_summary(None) is None


def test_get_job_run_front_loads_executor_oom() -> None:
    client = SparkK8sClient(SparkK8sConfig(namespace="cryo"))
    core_api = MagicMock()
    core_api.read_namespaced_pod.return_value = SimpleNamespace(
        status=SimpleNamespace(phase="Failed", container_statuses=[])
    )
    with (
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
        patch("cryo.engines.spark.k8s._read_pod_log_tail", return_value=_EXECUTOR_OOM_LOG),
    ):
        result = client.get_job_run("cryo-spark-x")
    assert result.state is JobRunState.FAILED
    assert (result.state_details or "").startswith("Executor OOMKilled exit 137 (executor 2)\n")


def test_get_job_run_reports_container_exit_when_log_has_no_exception() -> None:
    client = SparkK8sClient(SparkK8sConfig(namespace="cryo"))
    core_api = MagicMock()
    terminated = SimpleNamespace(reason="Error", exit_code=1)
    core_api.read_namespaced_pod.return_value = SimpleNamespace(
        status=SimpleNamespace(
            phase="Failed",
            container_statuses=[SimpleNamespace(state=SimpleNamespace(terminated=terminated))],
        )
    )
    info_tail = "INFO TaskSetManager: Finished task 265.0 in stage 1.0\n" * 20
    with (
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
        patch("cryo.engines.spark.k8s._read_pod_log_tail", return_value=info_tail),
    ):
        result = client.get_job_run("cryo-spark-x")
    assert (result.state_details or "").startswith("driver container terminated: Error exit 1\n")


def test_execute_retries_once_on_a_doubled_executor_heap_then_names_the_kill() -> None:
    engine, client = _engine({"total_size_gb": 120.0, "total_files": 4000})
    client.submit_spark_sql.side_effect = ["job-0", "job-1"]
    oom = JobRunResult(
        job_run_id="job-0",
        state=JobRunState.FAILED,
        state_details=f"Executor OOMKilled exit 137 (executor 2)\n{_EXECUTOR_OOM_LOG}",
    )
    client.wait_for_completion.side_effect = [oom, oom]

    result = engine.execute(engine.render(_optimize_plan())[0])

    assert not result.ok
    first, second = client.submit_spark_sql.call_args_list
    assert first.kwargs["executor_oom_steps"] == 0
    assert second.kwargs["executor_oom_steps"] == 1
    assert second.kwargs["oom_steps"] == 0
    error = result.error or ""
    assert error.startswith(
        "[root cause] Executor OOMKilled exit 137 (executor 2) after one retry on a larger "
        "executor heap\n"
    )
    assert "[root cause] Caused by: java.lang.NullPointerException" not in error


def test_execute_succeeds_after_executor_oom_retry() -> None:
    engine, client = _engine({"total_size_gb": 120.0})
    client.submit_spark_sql.side_effect = ["job-0", "job-1"]
    client.wait_for_completion.side_effect = [
        JobRunResult(
            job_run_id="job-0",
            state=JobRunState.FAILED,
            state_details="Executor OOMKilled exit 137 (executor 1)",
        ),
        JobRunResult(job_run_id="job-1", state=JobRunState.SUCCESS),
    ]
    assert engine.execute(engine.render(_optimize_plan())[0]).ok


def test_expire_does_not_take_an_executor_step() -> None:
    """Expire and orphan run one fixed small executor; stepping it would change nothing."""
    engine, client = _engine({"total_files": 1000})
    client.submit_spark_sql.return_value = "job-0"
    client.wait_for_completion.return_value = JobRunResult(
        job_run_id="job-0",
        state=JobRunState.FAILED,
        state_details="Executor OOMKilled exit 137 (executor 1)",
    )
    plan = MaintenancePlan(
        table_ref="cdp.mart.events",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),),
    )
    engine.execute(engine.render(plan)[0])
    client.submit_spark_sql.assert_called_once()


def test_executor_heap_step_doubles_and_caps() -> None:
    assert executor_heap_after_oom(4096, 0) == 4096
    assert executor_heap_after_oom(4096, 1) == 8192
    assert executor_heap_after_oom(8192, 1) == 12288
    assert executor_heap_after_oom(12288, 1) == 12288
    cfg = SparkK8sConfig(namespace="cryo", executor_mem_mib=4096)
    base = _capped_submit_config(cfg, operation="optimize", total_size_gb=120.0)
    stepped = _capped_submit_config(
        cfg, operation="optimize", total_size_gb=120.0, executor_oom_steps=1
    )
    assert stepped.executor_mem_mib == 2 * base.executor_mem_mib
    assert is_spark_executor_oom("Executor OOMKilled exit 137\nmore")
    assert not is_spark_executor_oom("OOMKilled exit 137")


def test_truncate_keeps_a_caller_named_root_cause() -> None:
    message = "[root cause] Executor OOMKilled exit 137\n" + "x" * 5000 + "\n" + _NPE
    assert truncate_error_message(message).startswith("[root cause] Executor OOMKilled")


# --- #159 quota refusals --------------------------------------------------------------------


def test_quota_refusal_is_recognised_and_described() -> None:
    assert is_capacity_refusal(_QUOTA_LOG)
    assert is_capacity_refusal('services "cryo-spark-abc-svc" is forbidden: exceeded quota: q')
    assert not is_capacity_refusal("AnalysisException: table not found")
    described = capacity_refusal_error(_QUOTA_LOG)
    assert described.startswith(CAPACITY_REFUSED_MARKER)
    assert "limits.cpu=48" in described
    assert capacity_refusal_error(described) == described


def test_execute_reports_a_driver_quota_refusal_as_capacity_without_retrying() -> None:
    engine, client = _engine()
    client.submit_spark_sql.return_value = "job-0"
    client.wait_for_completion.return_value = JobRunResult(
        job_run_id="job-0", state=JobRunState.FAILED, state_details=_QUOTA_LOG
    )
    result = engine.execute(engine.render(_optimize_plan())[0])
    assert not result.ok
    assert result.outcome_known
    assert (result.error or "").startswith(CAPACITY_REFUSED_MARKER)
    client.submit_spark_sql.assert_called_once()


def test_execute_reports_a_refused_dispatch_job_as_capacity() -> None:
    engine, client = _engine()
    client.submit_spark_sql.side_effect = RuntimeError(
        '(403) Reason: Forbidden: jobs.batch "cryo-spark-x" is forbidden: exceeded quota: q'
    )
    result = engine.execute(engine.render(_optimize_plan())[0])
    assert result.outcome_known
    assert (result.error or "").startswith(CAPACITY_REFUSED_MARKER)
    submitted = engine.submit(engine.render(_optimize_plan())[0])
    assert submitted.outcome_known
    assert (submitted.error or "").startswith(CAPACITY_REFUSED_MARKER)


# --- #160 sizing-key validation -------------------------------------------------------------


def test_flat_sizing_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="config.executor_cpu_milli, config.executor_instances"):
        DestinationCreate(
            name="x",
            engine=EngineType.SPARK_EMR,
            config={"submit_mode": "k8s", "executor_instances": 16, "executor_cpu_milli": 4000},
        )


def test_misspelled_spark_k8s_key_is_rejected_with_a_suggestion() -> None:
    with pytest.raises(ValueError, match="Did you mean 'executor_cpu_milli'"):
        DestinationCreate(
            name="x",
            engine=EngineType.SPARK_EMR,
            config={"submit_mode": "k8s", "spark_k8s": {"executor_cpu_mili": 4000}},
        )


def test_valid_spark_k8s_config_and_non_k8s_destinations_pass() -> None:
    validate_destination_config(
        EngineType.SPARK_EMR,
        {
            "submit_mode": "k8s",
            "catalog_uri": "http://rest:8181",
            "spark_k8s": {
                "executor_instances": 16,
                "executor_cpu_milli": 4000,
                "dynamic_sizing": False,
                "node_selector": {"pool": "spark"},
            },
        },
    )
    # EMR Serverless destinations are not Spark-on-K8s; their config is not checked here.
    validate_destination_config(EngineType.SPARK_EMR, {"executor_instances": 16})


@pytest.mark.parametrize("value", [0, -1, "lots", True, 1.5])
def test_bad_sizing_values_are_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        validate_destination_config(
            EngineType.SPARK_EMR,
            {"submit_mode": "k8s", "spark_k8s": {"executor_instances": value}},
        )


# --- #155 rewrite metrics -------------------------------------------------------------------


def _result_line(payload: dict[str, object]) -> str:
    return "[cryo-result] " + json.dumps(payload)


def test_result_metrics_from_log_reads_the_last_successful_payload() -> None:
    log = "\n".join(
        [
            "INFO starting",
            _result_line({"ok": True, "metrics": {"rewritten_data_files_count": 1}}),
            _result_line(
                {
                    "ok": True,
                    "action": "optimize",
                    "metrics": {
                        "rewritten_data_files_count": 1024,
                        "added_data_files_count": 17,
                        "rewritten_bytes_count": 9_000_000,
                    },
                }
            ),
            _result_line({"ok": False, "error": "later failure"}),
        ]
    )
    metrics = result_metrics_from_log(log)
    assert metrics is not None
    assert rewrite_file_counts("optimize", metrics) == (1024, 17)
    assert rewrite_file_counts("expire_snapshots", metrics) == (None, None)
    assert result_metrics_from_log("no result line") is None


def test_get_job_run_and_execute_carry_rewrite_file_counts() -> None:
    log = _result_line(
        {
            "ok": True,
            "metrics": {"rewritten_data_files_count": 1024, "added_data_files_count": 17},
            "bytes_removed": None,
        }
    )
    client = SparkK8sClient(SparkK8sConfig(namespace="cryo"))
    core_api = MagicMock()
    core_api.read_namespaced_pod.return_value = SimpleNamespace(
        status=SimpleNamespace(phase="Succeeded")
    )
    with (
        patch("cryo.engines.spark.k8s._new_k8s_core_client", return_value=core_api),
        patch("cryo.engines.spark.k8s._read_pod_log_tail", return_value=log),
    ):
        run = client.get_job_run("cryo-spark-x")
    assert run.metrics == {"rewritten_data_files_count": 1024, "added_data_files_count": 17}

    engine, mock_client = _engine()
    mock_client.submit_spark_sql.return_value = "job-0"
    mock_client.wait_for_completion.return_value = run
    result = engine.execute(engine.render(_optimize_plan())[0])
    assert result.ok
    assert (result.input_files_count, result.output_files_count) == (1024, 17)


# --- #159 worker defers instead of failing ---------------------------------------------------


def test_worker_defers_a_job_whose_every_failure_is_a_quota_refusal() -> None:
    from cryo.engines.base import ExecResult
    from cryo.worker.worker import FakeMaintenanceEngine, Worker
    from tests.test_worker import _worker_fixture

    class _QuotaRefused(FakeMaintenanceEngine):
        def execute(self, stmt, ctx=None):  # type: ignore[no-untyped-def]
            self.executed.append(stmt)
            return ExecResult(ok=False, error=capacity_refusal_error(_QUOTA_LOG))

    queue, locks, store = _worker_fixture()
    result = Worker(queue, store, lambda destination: _QuotaRefused(), locks).process_next()

    assert result is not None
    assert result.status is JobStatus.DEFERRED
    assert (result.error or "").startswith(f"capacity_deferred: {CAPACITY_REFUSED_MARKER}")
    assert {record.status for record in store.audit} == {"DEFERRED"}
    assert not locks.is_table_locked("table-1")


def test_worker_still_fails_a_job_with_a_real_error_next_to_a_quota_refusal() -> None:
    from cryo.engines.base import ExecResult
    from cryo.worker.worker import FakeMaintenanceEngine, Worker
    from tests.test_worker import _worker_fixture

    class _Mixed(FakeMaintenanceEngine):
        def execute(self, stmt, ctx=None):  # type: ignore[no-untyped-def]
            self.executed.append(stmt)
            if stmt.action is OperationType.OPTIMIZE:
                return ExecResult(ok=False, error=capacity_refusal_error(_QUOTA_LOG))
            return ExecResult(ok=False, error="AnalysisException: boom")

    queue, locks, store = _worker_fixture()
    result = Worker(queue, store, lambda destination: _Mixed(), locks).process_next()

    assert result is not None
    assert result.status is JobStatus.FAILED
