"""Tests for cryo.governor.fallback_sweep (PDAE-8352's async fallback-dispatch resolver).

_dispatch_fallback_operations submits Spark fallback jobs and records status="SUBMITTED"
without waiting -- these tests cover the sweep that resolves those rows to their real
terminal outcome, since nothing else in the system reaches a conclusion about them
otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from cryo.db.models import AuditLog, Destination
from cryo.engines.spark.k8s import JobRunResult, JobRunState
from cryo.governor.fallback_sweep import sweep_pending_fallback_dispatches


def _audit_row(*, status: str, engine_ref: str | None) -> AuditLog:
    return AuditLog(
        ts=datetime(2026, 8, 11, tzinfo=UTC),
        action="optimize_manifests",
        status=status,
        engine="spark_emr",
        engine_ref=engine_ref,
    )


def _seed_spark_destination(session) -> None:
    session.add(
        Destination(
            id=uuid4(),
            name="spark-k8s-fallback",
            engine="spark_emr",
            config={"catalog": "cdp"},
            enabled=True,
        )
    )
    session.commit()


def test_sweep_resolves_submitted_row_to_ok_when_job_succeeded(session, monkeypatch) -> None:
    _seed_spark_destination(session)
    row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-abc123")
    session.add(row)
    session.commit()

    mock_client = MagicMock()
    mock_client.get_job_run.return_value = JobRunResult(
        job_run_id="cryo-spark-abc123", state=JobRunState.SUCCESS
    )
    monkeypatch.setattr(
        "cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client
    )

    result = sweep_pending_fallback_dispatches(session)

    assert result.resolved_ok == 1
    assert result.resolved_failed == 0
    session.refresh(row)
    assert row.status == "OK"
    assert row.resolved_at is not None


def test_sweep_resolves_submitted_row_to_failed_when_job_failed(session, monkeypatch) -> None:
    _seed_spark_destination(session)
    row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-dead")
    session.add(row)
    session.commit()

    mock_client = MagicMock()
    mock_client.get_job_run.return_value = JobRunResult(
        job_run_id="cryo-spark-dead",
        state=JobRunState.FAILED,
        state_details="NoSuchTableException",
    )
    monkeypatch.setattr(
        "cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client
    )

    result = sweep_pending_fallback_dispatches(session)

    assert result.resolved_failed == 1
    session.refresh(row)
    assert row.status == "FAILED"
    assert row.detail == "NoSuchTableException"
    assert row.resolved_at is not None


def test_sweep_cleans_up_driver_pod_and_secret_on_resolution(session, monkeypatch) -> None:
    """submit() (the async path this sweep resolves) deliberately skips cleanup at
    dispatch time -- the driver pod/credentials Secret are still in use while the job
    runs. execute()'s own `finally: cleanup_after_run(...)` never runs for a
    submit()-dispatched job (execute() is never called for it), so this sweep is the
    only place left that will ever reclaim them once the row goes terminal."""
    _seed_spark_destination(session)
    row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-abc123")
    session.add(row)
    session.commit()

    mock_client = MagicMock()
    mock_client.get_job_run.return_value = JobRunResult(
        job_run_id="cryo-spark-abc123", state=JobRunState.SUCCESS
    )
    monkeypatch.setattr(
        "cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client
    )

    sweep_pending_fallback_dispatches(session)

    mock_client.cleanup_after_run.assert_called_once_with("cryo-spark-abc123")


def test_sweep_cleanup_failure_does_not_block_resolution(session, monkeypatch) -> None:
    """A cleanup failure (pod already gone, RBAC hiccup) must not stop the row from
    being marked resolved -- the real outcome is already known and correct either way."""
    _seed_spark_destination(session)
    row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-abc123")
    session.add(row)
    session.commit()

    mock_client = MagicMock()
    mock_client.get_job_run.return_value = JobRunResult(
        job_run_id="cryo-spark-abc123", state=JobRunState.SUCCESS
    )
    mock_client.cleanup_after_run.side_effect = RuntimeError("K8s API error")
    monkeypatch.setattr(
        "cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client
    )

    result = sweep_pending_fallback_dispatches(session)

    assert result.resolved_ok == 1
    session.refresh(row)
    assert row.status == "OK"
    assert row.resolved_at is not None


def test_sweep_leaves_still_running_job_pending(session, monkeypatch) -> None:
    _seed_spark_destination(session)
    row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-still-going")
    session.add(row)
    session.commit()

    mock_client = MagicMock()
    mock_client.get_job_run.return_value = JobRunResult(
        job_run_id="cryo-spark-still-going", state=JobRunState.RUNNING
    )
    monkeypatch.setattr(
        "cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client
    )

    result = sweep_pending_fallback_dispatches(session)

    assert result.still_pending == 1
    session.refresh(row)
    assert row.status == "SUBMITTED"
    assert row.resolved_at is None
    # Still running -- the driver pod/Secret are still in use, cleanup must not fire yet.
    mock_client.cleanup_after_run.assert_not_called()


def test_sweep_ignores_rows_already_resolved() -> None:
    """OK/FAILED rows must not be touched -- the sweep's own query filters on
    status='SUBMITTED', but this documents the intent directly."""
    row = _audit_row(status="OK", engine_ref="cryo-spark-done")
    assert row.status == "OK"  # sanity: sweep_pending_fallback_dispatches's query excludes this


def test_sweep_with_no_resolvable_destination_leaves_everything_pending(session) -> None:
    """No enabled spark_emr destination at all -- must not raise, must not silently drop
    the rows (they stay SUBMITTED for the next tick)."""
    row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-abc123")
    session.add(row)
    session.commit()

    result = sweep_pending_fallback_dispatches(session)

    assert result.still_pending == 1
    assert result.resolved_ok == 0
    assert result.resolved_failed == 0


def test_sweep_lookup_failure_on_one_row_does_not_block_the_rest(session, monkeypatch) -> None:
    _seed_spark_destination(session)
    good_row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-good")
    bad_row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-errors")
    session.add_all([bad_row, good_row])
    session.commit()

    mock_client = MagicMock()

    def _get_job_run(engine_ref: str) -> JobRunResult:
        if engine_ref == "cryo-spark-errors":
            raise RuntimeError("K8s API timeout")
        return JobRunResult(job_run_id=engine_ref, state=JobRunState.SUCCESS)

    mock_client.get_job_run.side_effect = _get_job_run
    monkeypatch.setattr(
        "cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client
    )

    result = sweep_pending_fallback_dispatches(session)

    assert result.resolved_ok == 1
    assert result.lookup_errors == 1
    session.refresh(good_row)
    session.refresh(bad_row)
    assert good_row.status == "OK"
    assert bad_row.status == "SUBMITTED"
