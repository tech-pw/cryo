"""SparkK8sEngine.execute_async: the blocking execute() split at its wait (#435)."""

from __future__ import annotations

from unittest.mock import MagicMock

from cryo.domain.enums import EngineType, OperationType
from cryo.engines.base import ExecResult, RenderedStatement
from cryo.engines.spark.async_state import SparkAsyncState
from cryo.engines.spark.k8s import JobRunResult, JobRunState
from cryo.engines.spark.k8s_engine import SparkK8sEngine


def _engine() -> tuple[SparkK8sEngine, MagicMock]:
    engine = SparkK8sEngine(config={"catalog": "analytics"})
    client = MagicMock()
    engine._client = client
    return engine, client


def _stmt() -> RenderedStatement:
    return RenderedStatement(
        action=OperationType.OPTIMIZE_MANIFESTS,
        sql="CALL analytics.system.rewrite_manifests('db.t')",
        engine=EngineType.SPARK_EMR,
    )


def test_submits_without_waiting_or_cleanup() -> None:
    engine, client = _engine()
    client.submit_spark_sql.return_value = "cryo-spark-1"

    state = engine.execute_async(_stmt())

    assert isinstance(state, SparkAsyncState)
    assert state.ref == "cryo-spark-1"
    assert state.deadline_seconds is not None
    client.wait_for_completion.assert_not_called()
    client.cleanup_after_run.assert_not_called()


def test_collects_the_same_result_execute_returns() -> None:
    engine, client = _engine()
    client.submit_spark_sql.return_value = "cryo-spark-1"
    running = JobRunResult(job_run_id="cryo-spark-1", state=JobRunState.RUNNING)
    done = JobRunResult(job_run_id="cryo-spark-1", state=JobRunState.SUCCESS, bytes_removed=99)

    state = engine.execute_async(_stmt())
    assert isinstance(state, SparkAsyncState)
    client.get_job_run.return_value = running
    assert engine.execute_async(_stmt(), None, state) == state
    client.cleanup_after_run.assert_not_called()

    client.get_job_run.return_value = done
    async_result = engine.execute_async(_stmt(), None, state)
    client.wait_for_completion.return_value = done
    sync_result = engine.execute(_stmt())

    assert isinstance(async_result, ExecResult)
    assert async_result.ok
    assert async_result.engine_ref == sync_result.engine_ref == "cryo-spark-1"
    assert async_result.actual_bytes_freed == sync_result.actual_bytes_freed == 99
    client.cleanup_after_run.assert_called_with("cryo-spark-1")


def test_catalog_retry_parks_instead_of_sleeping() -> None:
    engine, client = _engine()
    client.submit_spark_sql.side_effect = ["cryo-spark-1", "cryo-spark-2"]
    state = engine.execute_async(_stmt())
    assert isinstance(state, SparkAsyncState)
    client.get_job_run.return_value = JobRunResult(
        job_run_id="cryo-spark-1",
        state=JobRunState.FAILED,
        state_details="org.apache.iceberg.exceptions.CommitFailedException: retry me",
    )

    waiting = engine.execute_async(_stmt(), None, state)

    assert isinstance(waiting, SparkAsyncState)
    assert waiting.ref is None and waiting.not_before is not None
    assert waiting.attempt == 1 and waiting.catalog_attempts == 1
    due = SparkAsyncState.from_dict({**waiting.to_dict(), "not_before": 0.0})
    resubmitted = engine.execute_async(_stmt(), None, due)
    assert isinstance(resubmitted, SparkAsyncState)
    assert resubmitted.ref == "cryo-spark-2"
    assert client.submit_spark_sql.call_args.kwargs["job_name"].endswith("-r1")


def test_times_out_at_the_job_deadline_like_execute() -> None:
    engine, client = _engine()
    client.get_job_run.return_value = JobRunResult(job_run_id="x", state=JobRunState.RUNNING)
    stale = SparkAsyncState(started_at=0.0, ref="x", submitted_at=0.0, deadline_seconds=60)

    result = engine.execute_async(_stmt(), None, stale)

    assert isinstance(result, ExecResult)
    assert not result.ok and not result.outcome_known
    assert "did not complete" in (result.error or "")
    client.cleanup_after_run.assert_called_with("x")
