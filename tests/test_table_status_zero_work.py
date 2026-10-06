"""table_status()'s zero-work guard for the Spark coordinator path.

Confirmed live 2026-09-30 on analytics.silver_explore_click: an OPTIMIZE statement can
return ok=True (the Iceberg rewrite_data_files CALL didn't throw) while its own returned
metrics show rewritten_data_files_count=0 -- the native engine already guards this exact
pattern (native_job_reported_success_with_zero_work); this is that guard for Spark.
"""

from __future__ import annotations

import uuid

from cryo.domain.enums import JobStatus, OperationType
from cryo.maintenance.coordinator import TableOutcome, table_status
from cryo.maintenance.results import StatementResult

TABLE_ID = uuid.uuid4()
JOB_ID = uuid.uuid4()


def _outcome(results: dict[int, StatementResult], statement_count: int | None = None) -> TableOutcome:
    return TableOutcome(
        table_id=TABLE_ID,
        name="demo.table",
        job_id=JOB_ID,
        lane=0,
        statement_count=statement_count if statement_count is not None else len(results),
        before_size_gb=1.0,
        before_files=1303,
        results=results,
    )


def _result(seq: int, action: str, *, ok: bool, metrics: dict | None = None, rows: int | None = None) -> StatementResult:
    return StatementResult(
        table_id=TABLE_ID,
        seq=seq,
        action=action,
        ok=ok,
        metrics=metrics or {},
        rows=rows,
    )


def test_optimize_with_real_rewrite_count_succeeds() -> None:
    outcome = _outcome(
        {0: _result(0, OperationType.OPTIMIZE.value, ok=True, metrics={"rewritten_data_files_count": 42})}
    )
    status, error = table_status(outcome)
    assert status is JobStatus.SUCCEEDED
    assert error is None


def test_optimize_reporting_ok_with_zero_rewritten_files_is_flagged_failed() -> None:
    outcome = _outcome(
        {0: _result(0, OperationType.OPTIMIZE.value, ok=True, metrics={"rewritten_data_files_count": 0})}
    )
    status, error = table_status(outcome)
    assert status is JobStatus.FAILED
    assert error is not None
    assert "spark_job_reported_success_with_zero_work" in error
    assert "optimize" in error


def test_optimize_with_missing_metrics_key_is_treated_as_zero_work() -> None:
    # No rewritten_data_files_count key at all -- same as an explicit 0, not a free pass.
    outcome = _outcome({0: _result(0, OperationType.OPTIMIZE.value, ok=True, metrics={})})
    status, _ = table_status(outcome)
    assert status is JobStatus.FAILED


def test_optimize_zero_work_alongside_another_real_success_is_partial() -> None:
    outcome = _outcome(
        {
            0: _result(0, OperationType.OPTIMIZE.value, ok=True, metrics={"rewritten_data_files_count": 0}),
            1: _result(1, OperationType.EXPIRE_SNAPSHOTS.value, ok=True, rows=3),
        }
    )
    status, error = table_status(outcome)
    assert status is JobStatus.PARTIAL
    assert error is not None
    assert "spark_job_reported_success_with_zero_work" in error


def test_optimize_manifests_zero_rewrite_is_not_flagged() -> None:
    # optimize_manifests legitimately can report ok=True with no metrics -- it isn't in the
    # scoped action list (only optimize/convert_deletes are), so this must NOT be downgraded.
    outcome = _outcome(
        {0: _result(0, OperationType.OPTIMIZE_MANIFESTS.value, ok=True, metrics={})}
    )
    status, error = table_status(outcome)
    assert status is JobStatus.SUCCEEDED
    assert error is None


def test_expire_snapshots_without_metrics_is_unaffected() -> None:
    # expire_snapshots reports via `rows`, not `metrics` -- confirm the guard doesn't touch it.
    outcome = _outcome({0: _result(0, OperationType.EXPIRE_SNAPSHOTS.value, ok=True, rows=5)})
    status, error = table_status(outcome)
    assert status is JobStatus.SUCCEEDED
    assert error is None


def test_convert_deletes_zero_work_is_flagged_same_as_optimize() -> None:
    outcome = _outcome(
        {0: _result(0, OperationType.CONVERT_DELETES.value, ok=True, metrics={"added_data_files_count": 0})}
    )
    status, error = table_status(outcome)
    assert status is JobStatus.FAILED
    assert error is not None
    assert "convert_deletes" in error


def test_real_failure_still_reported_normally_when_no_zero_work() -> None:
    outcome = _outcome(
        {0: _result(0, OperationType.OPTIMIZE.value, ok=False, metrics={})}
    )
    outcome.results[0] = StatementResult(
        table_id=TABLE_ID, seq=0, action=OperationType.OPTIMIZE.value, ok=False, error="boom"
    )
    status, error = table_status(outcome)
    assert status is JobStatus.FAILED
    assert error == "boom"


def test_skipped_statements_still_report_skipped() -> None:
    outcome = _outcome(
        {0: StatementResult(table_id=TABLE_ID, seq=0, action=OperationType.OPTIMIZE.value, ok=False, skipped=True, error="not started")}
    )
    status, error = table_status(outcome)
    assert status is JobStatus.SKIPPED
    assert error == "not started"
