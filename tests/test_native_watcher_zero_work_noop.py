"""Tests for NativeJobWatcher's zero-work no-op detection in _finalize_success.

Real incident, 2026-09-15/16: gold_dbt_poll_dashboard_view's native-engine job reported
status=succeeded, with a real, correctly-planned operation list (rewrite_position_deletes,
optimize, compute_statistics, expire_snapshots, remove_orphan_files), ran for 25 minutes -- but
the table's live Iceberg $snapshots history showed zero new commits. The last real snapshot was
still 5 days old, and $files showed the exact same 53 data files / 10,731 delete files before
and after. The Go native binary self-reported success=true on every operation while doing
nothing, and _finalize_success trusted that report verbatim: it never cross-checked the binary's
own work-counters (files_rewritten, bytes_removed, snapshots_expired, etc.) against what a real
mutation of that operation type must produce.

This fix treats a per-operation success=true with an all-zero/absent work-counter (for operation
types that always do measurable work when actually run, e.g. rewrite_position_deletes,
optimize, expire_snapshots) as a suspicious no-op rather than a clean success -- the job is
marked FAILED with a distinct native_job_reported_success_with_zero_work error instead of
SUCCEEDED, so it surfaces for retry/investigation instead of silently vanishing for days.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryo.domain.enums import JobStatus, OperationType
from cryo.engines.native.watcher import (
    NativeJobWatcher,
    _native_operation_type,
    _reported_zero_work_operations,
)
from cryo.queue.locks import InMemoryLockStore


class TestNativeOperationTypeAliases:
    def test_rewrite_position_deletes_maps_to_its_own_operation_type(self) -> None:
        """Regression: this alias was missing entirely before the fix, so audit records for
        rewrite_position_deletes (the exact operation in the incident) were silently dropped."""
        assert (
            _native_operation_type("rewrite_position_deletes")
            == OperationType.REWRITE_POSITION_DELETES
        )

    def test_compute_statistics_maps_to_its_own_operation_type(self) -> None:
        assert _native_operation_type("compute_statistics") == OperationType.COMPUTE_STATISTICS


class TestReportedZeroWorkOperations:
    def test_rewrite_position_deletes_with_zero_files_rewritten_is_flagged(self) -> None:
        entry = {
            "ok": True,
            "results": [
                {"operation": "rewrite_position_deletes", "success": True, "files_rewritten": 0}
            ],
        }
        assert _reported_zero_work_operations(entry) == ["rewrite_position_deletes"]

    def test_rewrite_position_deletes_with_real_work_is_not_flagged(self) -> None:
        entry = {
            "ok": True,
            "results": [
                {
                    "operation": "rewrite_position_deletes",
                    "success": True,
                    "files_rewritten": 53,
                    "bytes_removed": 12_000_000,
                }
            ],
        }
        assert _reported_zero_work_operations(entry) == []

    def test_compute_statistics_success_with_no_counters_is_not_flagged(self) -> None:
        """compute_statistics never mutates data/snapshots by design -- it has no work-counter
        mapping, so a bare success=true with no file/byte counters is normal, not suspicious."""
        entry = {"ok": True, "results": [{"operation": "compute_statistics", "success": True}]}
        assert _reported_zero_work_operations(entry) == []

    def test_expire_snapshots_with_zero_snapshots_expired_is_flagged(self) -> None:
        entry = {
            "ok": True,
            "results": [{"operation": "expire_snapshots", "success": True, "snapshots_expired": 0}],
        }
        assert _reported_zero_work_operations(entry) == ["expire_snapshots"]

    def test_a_failed_operation_is_not_flagged_as_a_noop(self) -> None:
        """This check only adds signal on top of a claimed per-operation success -- it must not
        double-count an operation that already honestly reported success=false."""
        entry = {
            "ok": False,
            "results": [
                {"operation": "optimize", "success": False, "files_rewritten": 0, "error": "boom"}
            ],
        }
        assert _reported_zero_work_operations(entry) == []

    def test_multiple_noop_operations_are_all_reported(self) -> None:
        entry = {
            "ok": True,
            "results": [
                {"operation": "rewrite_position_deletes", "success": True, "files_rewritten": 0},
                {"operation": "expire_snapshots", "success": True, "snapshots_expired": 0},
                {"operation": "compute_statistics", "success": True},
            ],
        }
        assert _reported_zero_work_operations(entry) == [
            "rewrite_position_deletes",
            "expire_snapshots",
        ]

    def test_no_results_at_all_is_not_flagged_here(self) -> None:
        """A job with no results is already handled by the existing native_job_no_result path
        in _record_result_audits -- this check must not pile a second, redundant failure on it."""
        assert _reported_zero_work_operations({"ok": True, "results": []}) == []
        assert _reported_zero_work_operations(None) == []


class TestFinalizeSuccessZeroWorkDowngrade:
    """End-to-end through NativeJobWatcher._finalize_success, same call shape as
    test_native_watcher_transient_metastore_retry.py's TestFinalizeSuccessRetryClassification."""

    def _make_watcher(
        self, statuses: list, audits: list, locks: InMemoryLockStore
    ) -> NativeJobWatcher:
        return NativeJobWatcher(
            session_factory=MagicMock(),
            lock_store=locks,
            mark_status=lambda job_id, status, error=None, **_: statuses.append(
                (job_id, status, error)
            ),
            record_audit=audits.append,
        )

    def test_gold_dbt_poll_dashboard_view_shape_is_marked_failed_not_succeeded(
        self, session
    ) -> None:
        """Direct regression test for the live incident: a batch-success job_id-level report
        (ok=true) whose per-operation results all report success=true but zero actual file/
        snapshot mutation must not be marked SUCCEEDED."""
        table_id = uuid.uuid4()
        job = SimpleNamespace(
            id=uuid.uuid4(), table_id=table_id, status=JobStatus.RUNNING.value, plan={}
        )
        locks = InMemoryLockStore()
        locks.acquire_table_lock(str(table_id), str(job.id), 3600)
        statuses: list = []
        audits: list = []
        watcher = self._make_watcher(statuses, audits, locks)

        stdout = (
            '{"batch":true,"results":[{"job_id":"%s","ok":true,"results":['
            '{"operation":"rewrite_position_deletes","success":true,"files_rewritten":0},'
            '{"operation":"optimize","success":true,"files_rewritten":0},'
            '{"operation":"compute_statistics","success":true},'
            '{"operation":"expire_snapshots","success":true,"snapshots_expired":0},'
            '{"operation":"remove_orphan_files","success":true,"bytes_removed":0}'
            "]}]}" % job.id
        )
        dispatch_id = uuid.uuid4()
        with (
            patch("cryo.engines.native.watcher.jobs_for_dispatch", return_value=[job]),
            patch("cryo.engines.native.watcher.mark_dispatch_finished") as finish,
        ):
            watcher._finalize_success(session, dispatch_id, stdout)

        assert len(statuses) == 1
        job_id, status, error = statuses[0]
        assert status == JobStatus.FAILED
        assert error is not None and "native_job_reported_success_with_zero_work" in error
        assert "rewrite_position_deletes" in error
        assert "optimize" in error
        assert "expire_snapshots" in error
        assert "remove_orphan_files" in error
        # compute_statistics never mutates state -- must not appear as a flagged no-op.
        assert (
            "compute_statistics"
            not in error.split("native_job_reported_success_with_zero_work: ")[1]
        )
        assert finish.call_args.kwargs["status"] == "failed"

    def test_real_rewrite_with_actual_files_still_succeeds(self, session) -> None:
        """Baseline: this fix must not regress the ordinary case -- a job that genuinely
        rewrote files must still be marked SUCCEEDED."""
        table_id = uuid.uuid4()
        job = SimpleNamespace(
            id=uuid.uuid4(), table_id=table_id, status=JobStatus.RUNNING.value, plan={}
        )
        locks = InMemoryLockStore()
        locks.acquire_table_lock(str(table_id), str(job.id), 3600)
        statuses: list = []
        audits: list = []
        watcher = self._make_watcher(statuses, audits, locks)

        stdout = (
            '{"batch":true,"results":[{"job_id":"%s","ok":true,"results":['
            '{"operation":"rewrite_position_deletes","success":true,"files_rewritten":53,'
            '"bytes_removed":12000000}'
            "]}]}" % job.id
        )
        dispatch_id = uuid.uuid4()
        with (
            patch("cryo.engines.native.watcher.jobs_for_dispatch", return_value=[job]),
            patch("cryo.engines.native.watcher.mark_dispatch_finished") as finish,
        ):
            watcher._finalize_success(session, dispatch_id, stdout)

        assert statuses == [(str(job.id), JobStatus.SUCCEEDED, None)]
        assert finish.call_args.kwargs["status"] == "succeeded"

    def test_compute_statistics_only_job_still_succeeds_with_no_counters(self, session) -> None:
        """A job whose only planned operation is compute_statistics (no data/snapshot mutation
        by design) must not be penalized for reporting no file/byte counters."""
        table_id = uuid.uuid4()
        job = SimpleNamespace(
            id=uuid.uuid4(), table_id=table_id, status=JobStatus.RUNNING.value, plan={}
        )
        locks = InMemoryLockStore()
        locks.acquire_table_lock(str(table_id), str(job.id), 3600)
        statuses: list = []
        audits: list = []
        watcher = self._make_watcher(statuses, audits, locks)

        stdout = (
            '{"batch":true,"results":[{"job_id":"%s","ok":true,"results":['
            '{"operation":"compute_statistics","success":true}'
            "]}]}" % job.id
        )
        dispatch_id = uuid.uuid4()
        with (
            patch("cryo.engines.native.watcher.jobs_for_dispatch", return_value=[job]),
            patch("cryo.engines.native.watcher.mark_dispatch_finished") as finish,
        ):
            watcher._finalize_success(session, dispatch_id, stdout)

        assert statuses == [(str(job.id), JobStatus.SUCCEEDED, None)]
        assert finish.call_args.kwargs["status"] == "succeeded"
