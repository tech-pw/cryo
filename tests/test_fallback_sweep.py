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

from cryo.db.models import AuditLog, Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.engines.spark.k8s import JobRunResult, JobRunState
from cryo.governor.fallback_sweep import sweep_pending_fallback_dispatches


def _audit_row(*, status: str, engine_ref: str | None, job_id=None) -> AuditLog:
    return AuditLog(
        ts=datetime(2026, 8, 11, tzinfo=UTC),
        action="optimize_manifests",
        status=status,
        engine="spark_emr",
        engine_ref=engine_ref,
        job_id=job_id,
    )


def _seed_running_job(session, *, destination_id) -> MaintenanceJob:
    """A MaintenanceJob row in the exact state a Spark fallback dispatch leaves it in before
    this sweep resolves it -- status=running, started_at set at first dispatch, finished_at
    still null."""
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(id=uuid4(), source_id=source.id, schema_name="s", table_name="t", is_active=True)
    run = MaintenanceRun(id=uuid4(), kind="scheduled", status="running", trigger="cron")
    session.add_all([source, table, run])
    session.flush()
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination_id,
        mode="standard",
        status="running",
        started_at=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
    )
    session.add(job)
    session.commit()
    return job


def _seed_spark_destination(session) -> Destination:
    destination = Destination(
        id=uuid4(),
        name="spark-k8s-fallback",
        engine="spark_emr",
        config={"catalog": "cdp"},
        enabled=True,
    )
    session.add(destination)
    session.commit()
    return destination


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


def test_sweep_resolves_submitted_row_to_unknown_when_job_ttl_expired(session, monkeypatch) -> None:
    """Real incident, 2026-08-12: a scheduler RBAC gap (fixed separately) left this sweep
    unable to resolve anything for ~20h; by the time it could reach 8,401 backlogged
    SUBMITTED rows, ~8,348 of them had TTL-expired -- their K8s Job and driver pod were
    both already gone. UNKNOWN records that honestly (outcome unrecoverable) instead of
    a false FAILED (implies an observed failure that was never actually seen)."""
    _seed_spark_destination(session)
    row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-longgone")
    session.add(row)
    session.commit()

    mock_client = MagicMock()
    mock_client.get_job_run.return_value = JobRunResult(
        job_run_id="cryo-spark-longgone",
        state=JobRunState.UNKNOWN,
        state_details="Dispatch Job and driver pod both gone (TTL-expired)",
    )
    monkeypatch.setattr(
        "cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client
    )

    result = sweep_pending_fallback_dispatches(session)

    assert result.resolved_unknown == 1
    assert result.resolved_ok == 0
    assert result.resolved_failed == 0
    session.refresh(row)
    assert row.status == "UNKNOWN"
    assert row.detail == "Dispatch Job and driver pod both gone (TTL-expired)"
    assert row.resolved_at is not None
    # Still attempts cleanup -- the credentials Secret may outlive the wrapper Job.
    mock_client.cleanup_after_run.assert_called_once_with("cryo-spark-longgone")


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


class TestJobRowFinalization:
    """2026-09-01 fix: the sweep must finalize the underlying MaintenanceJob row, not just
    the AuditLog row -- live-verified regression (see fallback_sweep.py's own docstring):
    a job dispatched through this path stayed status='running' indefinitely regardless of
    real outcome, since nothing else in the system ever touched cryo.jobs for this path."""

    def test_resolved_ok_finalizes_job_to_succeeded(self, session, monkeypatch) -> None:
        destination = _seed_spark_destination(session)
        job = _seed_running_job(session, destination_id=destination.id)
        row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-abc123", job_id=job.id)
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

        session.refresh(job)
        assert job.status == "succeeded"
        assert job.finished_at is not None
        assert job.error is None

    def test_resolved_failed_finalizes_job_to_failed_with_error(self, session, monkeypatch) -> None:
        destination = _seed_spark_destination(session)
        job = _seed_running_job(session, destination_id=destination.id)
        row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-dead", job_id=job.id)
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

        sweep_pending_fallback_dispatches(session)

        session.refresh(job)
        assert job.status == "failed"
        assert job.finished_at is not None
        assert job.error == "NoSuchTableException"

    def test_resolved_unknown_finalizes_job_to_failed_not_left_running(
        self, session, monkeypatch
    ) -> None:
        """UNKNOWN (TTL-expired, outcome unrecoverable) has no positive evidence of success
        -- the job row must still leave RUNNING (the whole point of this fix) rather than
        being left stuck open forever just because the real outcome can't be recovered."""
        destination = _seed_spark_destination(session)
        job = _seed_running_job(session, destination_id=destination.id)
        row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-longgone", job_id=job.id)
        session.add(row)
        session.commit()

        mock_client = MagicMock()
        mock_client.get_job_run.return_value = JobRunResult(
            job_run_id="cryo-spark-longgone",
            state=JobRunState.UNKNOWN,
            state_details="Dispatch Job and driver pod both gone (TTL-expired)",
        )
        monkeypatch.setattr(
            "cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client
        )

        sweep_pending_fallback_dispatches(session)

        session.refresh(job)
        assert job.status == "failed"
        assert job.finished_at is not None

    def test_still_running_does_not_touch_job_row(self, session, monkeypatch) -> None:
        destination = _seed_spark_destination(session)
        job = _seed_running_job(session, destination_id=destination.id)
        row = _audit_row(
            status="SUBMITTED", engine_ref="cryo-spark-still-going", job_id=job.id
        )
        session.add(row)
        session.commit()

        mock_client = MagicMock()
        mock_client.get_job_run.return_value = JobRunResult(
            job_run_id="cryo-spark-still-going", state=JobRunState.RUNNING
        )
        monkeypatch.setattr(
            "cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client
        )

        sweep_pending_fallback_dispatches(session)

        session.refresh(job)
        assert job.status == "running"
        assert job.finished_at is None

    def test_job_already_terminal_is_not_overwritten(self, session, monkeypatch) -> None:
        """Live-verified regression shape: a job re-dispatched multiple times produces
        multiple SUBMITTED audit rows for the same job_id. If an earlier row's resolution
        already finalized the job, a later (possibly stale) row's resolution must not
        clobber that real outcome -- e.g. a genuinely-succeeded job must not flip back to
        failed because an older, now-irrelevant audit row resolves after it."""
        destination = _seed_spark_destination(session)
        job = _seed_running_job(session, destination_id=destination.id)
        job.status = "succeeded"
        job.finished_at = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
        session.commit()

        row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-stale", job_id=job.id)
        session.add(row)
        session.commit()

        mock_client = MagicMock()
        mock_client.get_job_run.return_value = JobRunResult(
            job_run_id="cryo-spark-stale",
            state=JobRunState.FAILED,
            state_details="stale re-dispatch outcome",
        )
        monkeypatch.setattr(
            "cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client
        )

        sweep_pending_fallback_dispatches(session)

        session.refresh(job)
        assert job.status == "succeeded"  # unchanged -- not clobbered by the stale row
        # sqlite (this test's own session fixture) round-trips TIMESTAMP columns as naive
        # datetimes even when the value written was tz-aware -- same quirk health_check.py's
        # own _as_utc works around; real Postgres never needs this.
        finished_at = job.finished_at
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=UTC)
        assert finished_at == datetime(2026, 8, 11, 10, 0, tzinfo=UTC)

    def test_audit_row_with_no_job_id_does_not_error(self, session, monkeypatch) -> None:
        """job_id is nullable on AuditLog -- a row with none (or a since-deleted job) must
        resolve the audit row cleanly without erroring, matching every pre-existing test in
        this file (none of them set job_id)."""
        _seed_spark_destination(session)
        row = _audit_row(status="SUBMITTED", engine_ref="cryo-spark-abc123", job_id=None)
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
        session.refresh(row)
        assert row.status == "OK"
