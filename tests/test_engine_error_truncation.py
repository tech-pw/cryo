"""Regression, 2026-07-27: a Trino GENERIC_INTERNAL_ERROR on reports.gold_dbt_video_stats_reports_unified
(1.5 TB, 44,818 files) serialized its full split-assignment file list into the exception
message. str(exc) alone produced a 2,962,267-character error, stored verbatim in
MaintenanceJob.error -- bloating the jobs table row, the audit log, and every API response
that includes it, while burying the one-line actionable root cause inside megabytes of Java
object dumps. Every engine's except-block must cap this via truncate_error_message before it
reaches ExecResult.error. Per-engine coverage lives in test_trino_engine.py and
test_spark_k8s_engine.py; this file covers the shared helper and the (previously untested)
EMR Spark engine.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.base import is_table_not_found_error, truncate_error_message
from cryo.engines.spark.emr import JobRunResult, JobRunState
from cryo.engines.spark.engine import SparkEMREngine


def test_truncate_error_message_passes_through_short_messages() -> None:
    assert truncate_error_message("boom") == "boom"


def test_truncate_error_message_caps_long_messages_with_a_count() -> None:
    huge = "x" * 3_000_000
    result = truncate_error_message(huge)

    assert len(result) < 4200
    assert result.startswith("x" * 100)
    assert "truncated" in result
    assert "3,000,000 chars total" in result


def test_truncate_error_message_respects_custom_limit() -> None:
    result = truncate_error_message("y" * 100, limit=10)
    assert result.startswith("y" * 10)
    assert "100 chars total" in result


def test_truncate_error_message_preserves_tail_not_just_head() -> None:
    """Regression, 2026-07-28: a Spark-on-K8s job's stored error was its raw stdout dump --
    INFO-level executor/block-manager registration chatter first, the real exception and
    stack trace last. Head-only truncation kept only the boilerplate and threw away the one
    part an operator (or Cryo itself, on the next diagnosis pass) actually needs. Confirmed
    live: a remove_orphan_files ClassNotFoundException was completely invisible in the stored
    error until this fix."""
    message = "INFO block manager registration chatter " * 200 + "Caused by: ClassNotFoundException: real cause"
    result = truncate_error_message(message)

    assert "ClassNotFoundException: real cause" in result
    assert result.endswith("ClassNotFoundException: real cause")
    assert result.startswith("INFO block manager registration chatter")
    assert "truncated" in result


def test_is_table_not_found_error_matches_known_signatures() -> None:
    assert is_table_not_found_error(
        "org.apache.spark.sql.catalyst.analysis.NoSuchTableException: "
        "[TABLE_OR_VIEW_NOT_FOUND] The table or view x.y cannot be found."
    )
    assert is_table_not_found_error("io.trino.spi.TrinoException: TABLE_OR_VIEW_NOT_FOUND")
    assert is_table_not_found_error("org.apache.iceberg.exceptions.TableNotFoundException: ...")


def test_is_table_not_found_error_false_for_unrelated_or_empty() -> None:
    assert not is_table_not_found_error("OutOfMemoryError: Java heap space")
    assert not is_table_not_found_error(None)
    assert not is_table_not_found_error("")


def _plan() -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="glue_catalog.reports.gold_dbt_video_stats_reports_unified",
        mode=Mode.DEEP,
        operations=(Operation(OperationType.OPTIMIZE, {}),),
    )


def test_emr_engine_truncates_job_state_details() -> None:
    """The EMR failure path builds its own f-string from result.state_details, a separate
    call site from the except-block -- must be truncated too, not just str(exc) paths."""
    engine = SparkEMREngine(config={})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.return_value = "run-123"
    mock_client.wait_for_completion.return_value = JobRunResult(
        job_run_id="run-123",
        state=JobRunState.FAILED,
        state_details="x" * 20_000,
    )
    engine._client = mock_client

    results = engine.execute_plan(_plan())

    assert len(results) == 1
    assert not results[0].ok
    assert len(results[0].error) < 4200
    assert "truncated" in results[0].error


def test_emr_engine_truncates_submission_exceptions() -> None:
    engine = SparkEMREngine(config={})
    mock_client = MagicMock()
    mock_client.submit_spark_sql.side_effect = RuntimeError("z" * 20_000)
    engine._client = mock_client

    results = engine.execute_plan(_plan())

    assert len(results) == 1
    assert not results[0].ok
    assert len(results[0].error) < 4200
    assert "EMR submission failed" in results[0].error
    assert "truncated" in results[0].error
