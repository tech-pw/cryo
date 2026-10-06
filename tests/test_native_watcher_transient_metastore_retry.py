"""Tests for NativeJobWatcher's transient-metastore-timeout retry classification.

Found live, 2026-08-31: 490 distinct tables hit the bounded connect-timeout error
("...failed to connect to metastore: connect timed out after 45s", the exact signature the
2026-08-21/28 timeout fixes produce) in a single 24h window. Before this fix, every one of
those was terminally marked FAILED on the very first occurrence -- a real, transient
metastore blip (still ongoing) cost each table a full dispatch slot for zero benefit, with no
retry until whatever cron/manual trigger happened to pick that table up again, sometimes 24h
later given the daily cron cadence.

_finalize_success now classifies a per-table failure whose error matches the transient-
metastore signature as DEFERRED instead of FAILED (when under the retry cap) --
reconcile_deferred_jobs (cryo/queue/reconcile.py, called every governor wave) already
automatically requeues DEFERRED jobs, so this alone recovers the retry without needing any new
scheduling mechanism. The cap (_METASTORE_RETRY_CAP, counted via real job history) exists so a
genuinely broken/unreachable table still surfaces as FAILED eventually rather than looping
queued->deferred->queued forever.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.domain.enums import JobStatus
from cryo.engines.native.watcher import (
    _METASTORE_RETRY_CAP,
    NativeJobWatcher,
    _is_transient_metastore_error,
    _under_retry_cap,
)
from cryo.queue.locks import InMemoryLockStore

_TRANSIENT_ERROR = (
    "failed to connect to catalog: after 1 attempts: failed to create Hive client: "
    "failed to connect to metastore: connect timed out after 45s"
)


def _seed_table(session) -> Table:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="rest", config={})
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="s",
        table_name="t",
        is_active=True,
    )
    session.add_all([source, table])
    session.flush()
    return table


def _seed_job(
    session,
    *,
    table: Table,
    status: str,
    error: str | None,
    finished_at: datetime | None,
) -> MaintenanceJob:
    destination = Destination(id=uuid.uuid4(), name=f"dest-{uuid.uuid4()}", engine="native", config={})
    run = MaintenanceRun(id=uuid.uuid4(), kind="scheduled", status="succeeded", trigger="cron")
    job = MaintenanceJob(
        id=uuid.uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status=status,
        error=error,
        finished_at=finished_at,
    )
    session.add_all([destination, run, job])
    session.flush()
    return job


class TestIsTransientMetastoreError:
    def test_matches_the_real_bounded_timeout_signature(self) -> None:
        assert _is_transient_metastore_error(_TRANSIENT_ERROR)

    def test_does_not_match_an_unrelated_error(self) -> None:
        assert not _is_transient_metastore_error("boom")

    def test_does_not_match_a_generic_transient_connect_error(self) -> None:
        """The pre-timeout-fix error shape ("connect: connection timed out", no bounded-timeout
        wording) must not match -- that's the OS-level default-timeout case the 2026-08-21 fix
        was built to eliminate, not the bounded-timeout signature this retry targets."""
        assert not _is_transient_metastore_error("connect: connection timed out")


class TestUnderRetryCap:
    def test_table_with_no_history_is_under_cap(self, session) -> None:
        table = _seed_table(session)
        session.commit()
        assert _under_retry_cap(session, table_id=table.id)

    def test_table_under_the_cap_stays_eligible(self, session) -> None:
        table = _seed_table(session)
        now = datetime.now(UTC)
        for _ in range(_METASTORE_RETRY_CAP - 1):
            _seed_job(
                session, table=table, status="deferred", error=_TRANSIENT_ERROR, finished_at=now
            )
        session.commit()
        assert _under_retry_cap(session, table_id=table.id)

    def test_table_at_the_cap_is_no_longer_eligible(self, session) -> None:
        table = _seed_table(session)
        now = datetime.now(UTC)
        for _ in range(_METASTORE_RETRY_CAP):
            _seed_job(
                session, table=table, status="deferred", error=_TRANSIENT_ERROR, finished_at=now
            )
        session.commit()
        assert not _under_retry_cap(session, table_id=table.id)

    def test_failed_status_counts_toward_the_cap_same_as_deferred(self, session) -> None:
        """A table that already exhausted its cap once (landed on FAILED) and is now being
        re-evaluated (e.g. re-triggered manually) must not get a fresh budget -- FAILED rows
        with this error signature count the same as DEFERRED ones."""
        table = _seed_table(session)
        now = datetime.now(UTC)
        for _ in range(_METASTORE_RETRY_CAP):
            _seed_job(
                session, table=table, status="failed", error=_TRANSIENT_ERROR, finished_at=now
            )
        session.commit()
        assert not _under_retry_cap(session, table_id=table.id)

    def test_old_failures_outside_the_lookback_window_do_not_count(self, session) -> None:
        table = _seed_table(session)
        long_ago = datetime.now(UTC) - timedelta(hours=48)
        for _ in range(_METASTORE_RETRY_CAP):
            _seed_job(
                session, table=table, status="deferred", error=_TRANSIENT_ERROR, finished_at=long_ago
            )
        session.commit()
        assert _under_retry_cap(session, table_id=table.id)

    def test_unrelated_error_history_does_not_count_toward_the_cap(self, session) -> None:
        table = _seed_table(session)
        now = datetime.now(UTC)
        for _ in range(_METASTORE_RETRY_CAP + 5):
            _seed_job(session, table=table, status="failed", error="boom", finished_at=now)
        session.commit()
        assert _under_retry_cap(session, table_id=table.id)

    def test_another_tables_history_does_not_count(self, session) -> None:
        table_a = _seed_table(session)
        table_b = _seed_table(session)
        now = datetime.now(UTC)
        for _ in range(_METASTORE_RETRY_CAP):
            _seed_job(
                session, table=table_a, status="deferred", error=_TRANSIENT_ERROR, finished_at=now
            )
        session.commit()
        assert _under_retry_cap(session, table_id=table_b.id)


class TestFinalizeSuccessRetryClassification:
    """End-to-end through NativeJobWatcher._finalize_success, same call shape as the existing
    test_watcher_preserves_partial_batch_success_and_audits in test_native_batch_and_dispatch.py."""

    def _make_watcher(self, statuses: list, audits: list, locks: InMemoryLockStore) -> NativeJobWatcher:
        return NativeJobWatcher(
            session_factory=MagicMock(),
            lock_store=locks,
            mark_status=lambda job_id, status, error=None, **_: statuses.append(
                (job_id, status, error)
            ),
            record_audit=audits.append,
        )

    def test_transient_metastore_failure_under_cap_is_deferred_not_failed(self, session) -> None:
        table = _seed_table(session)
        session.commit()

        from types import SimpleNamespace

        job = SimpleNamespace(
            id=uuid.uuid4(), table_id=table.id, status=JobStatus.RUNNING.value, plan={}
        )
        locks = InMemoryLockStore()
        locks.acquire_table_lock(str(table.id), str(job.id), 3600)
        statuses: list = []
        audits: list = []
        watcher = self._make_watcher(statuses, audits, locks)

        stdout = (
            '{"ok":false,"batch":true,"results":['
            f'{{"job_id":"{job.id}","ok":false,"error":"{_TRANSIENT_ERROR}","results":[]}}]}}'
        )
        dispatch_id = uuid.uuid4()
        with (
            patch("cryo.engines.native.watcher.jobs_for_dispatch", return_value=[job]),
            patch("cryo.engines.native.watcher.mark_dispatch_finished") as finish,
        ):
            watcher._finalize_success(session, dispatch_id, stdout)

        assert statuses == [(str(job.id), JobStatus.DEFERRED, _TRANSIENT_ERROR)]
        assert finish.call_args.kwargs["status"] == "partial"

    def test_transient_metastore_failure_at_cap_is_failed_not_deferred(self, session) -> None:
        table = _seed_table(session)
        now = datetime.now(UTC)
        for _ in range(_METASTORE_RETRY_CAP):
            _seed_job(
                session, table=table, status="deferred", error=_TRANSIENT_ERROR, finished_at=now
            )
        session.commit()

        from types import SimpleNamespace

        job = SimpleNamespace(
            id=uuid.uuid4(), table_id=table.id, status=JobStatus.RUNNING.value, plan={}
        )
        locks = InMemoryLockStore()
        locks.acquire_table_lock(str(table.id), str(job.id), 3600)
        statuses: list = []
        audits: list = []
        watcher = self._make_watcher(statuses, audits, locks)

        stdout = (
            '{"ok":false,"batch":true,"results":['
            f'{{"job_id":"{job.id}","ok":false,"error":"{_TRANSIENT_ERROR}","results":[]}}]}}'
        )
        dispatch_id = uuid.uuid4()
        with (
            patch("cryo.engines.native.watcher.jobs_for_dispatch", return_value=[job]),
            patch("cryo.engines.native.watcher.mark_dispatch_finished") as finish,
        ):
            watcher._finalize_success(session, dispatch_id, stdout)

        assert statuses == [(str(job.id), JobStatus.FAILED, _TRANSIENT_ERROR)]
        assert finish.call_args.kwargs["status"] == "failed"

    def test_non_transient_failure_is_still_terminally_failed(self, session) -> None:
        """Baseline: an ordinary failure (not the transient-metastore signature) must behave
        exactly as before this fix -- straight to FAILED, no deferral."""
        table = _seed_table(session)
        session.commit()

        from types import SimpleNamespace

        job = SimpleNamespace(
            id=uuid.uuid4(), table_id=table.id, status=JobStatus.RUNNING.value, plan={}
        )
        locks = InMemoryLockStore()
        locks.acquire_table_lock(str(table.id), str(job.id), 3600)
        statuses: list = []
        audits: list = []
        watcher = self._make_watcher(statuses, audits, locks)

        stdout = (
            '{"ok":false,"batch":true,"results":['
            f'{{"job_id":"{job.id}","ok":false,"error":"boom","results":[]}}]}}'
        )
        dispatch_id = uuid.uuid4()
        with (
            patch("cryo.engines.native.watcher.jobs_for_dispatch", return_value=[job]),
            patch("cryo.engines.native.watcher.mark_dispatch_finished") as finish,
        ):
            watcher._finalize_success(session, dispatch_id, stdout)

        assert statuses == [(str(job.id), JobStatus.FAILED, "boom")]
        assert finish.call_args.kwargs["status"] == "failed"

    def test_mix_of_success_and_deferred_reports_partial_not_failed(self, session) -> None:
        """The dispatch-level status must reflect that one member is still in play (deferred,
        will be retried), not be dragged down to a flat 'failed' report."""
        table_a = _seed_table(session)
        table_b = _seed_table(session)
        session.commit()

        from types import SimpleNamespace

        job_ok = SimpleNamespace(
            id=uuid.uuid4(), table_id=table_a.id, status=JobStatus.RUNNING.value, plan={}
        )
        job_deferred = SimpleNamespace(
            id=uuid.uuid4(), table_id=table_b.id, status=JobStatus.RUNNING.value, plan={}
        )
        locks = InMemoryLockStore()
        locks.acquire_table_lock(str(table_a.id), str(job_ok.id), 3600)
        locks.acquire_table_lock(str(table_b.id), str(job_deferred.id), 3600)
        statuses: list = []
        audits: list = []
        watcher = self._make_watcher(statuses, audits, locks)

        stdout = (
            '{"ok":false,"batch":true,"results":['
            f'{{"job_id":"{job_ok.id}","ok":true,"results":[]}},'
            f'{{"job_id":"{job_deferred.id}","ok":false,"error":"{_TRANSIENT_ERROR}","results":[]}}'
            "]}"
        )
        dispatch_id = uuid.uuid4()
        with (
            patch(
                "cryo.engines.native.watcher.jobs_for_dispatch",
                return_value=[job_ok, job_deferred],
            ),
            patch("cryo.engines.native.watcher.mark_dispatch_finished") as finish,
        ):
            watcher._finalize_success(session, dispatch_id, stdout)

        assert (str(job_ok.id), JobStatus.SUCCEEDED, None) in statuses
        assert (str(job_deferred.id), JobStatus.DEFERRED, _TRANSIENT_ERROR) in statuses
        assert finish.call_args.kwargs["status"] == "partial"
        # The deferred member must not show up in the dispatch's own error summary -- it isn't
        # a hard failure, it's a scheduled retry.
        assert finish.call_args.kwargs["error"] is None
