"""Regression, 2026-07-27: a Trino GENERIC_INTERNAL_ERROR on
reports.gold_dbt_video_stats_reports_unified (1.5 TB, 44,818 files) serialized its full
split-assignment file list into the exception message. str(exc) alone produced a
2,962,267-character error, stored verbatim in
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
from cryo.engines.base import deepest_caused_by, is_table_not_found_error, truncate_error_message
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
    error until this fix.

    Since the 2026-08-11 fix (see test_truncate_error_message_preserves_caused_by_buried_in_
    the_middle), the deepest `Caused by:` line is now front-loaded with a `[root cause]`
    prefix -- so the result starts with that prefix, then the original head text, rather than
    starting directly with the boilerplate. The original (still-true) guarantee this test
    exists for is that the real cause survives at all and the boilerplate head is still kept
    too, not that nothing gets prepended ahead of it."""
    message = (
        "INFO block manager registration chatter " * 200
        + "Caused by: ClassNotFoundException: real cause"
    )
    result = truncate_error_message(message)

    assert "ClassNotFoundException: real cause" in result
    assert result.endswith("ClassNotFoundException: real cause")
    assert result.startswith("[root cause] Caused by: ClassNotFoundException: real cause")
    assert "INFO block manager registration chatter" in result
    assert "truncated" in result


def test_truncate_error_message_preserves_caused_by_buried_in_the_middle() -> None:
    """Regression, confirmed live 2026-08-11: ~40 Spark-on-K8s driver failures on
    spark-k8s-fallback had their `error` field truncated at exactly 4,051 chars with no
    `Caused by:` line anywhere in the stored text -- a BaseMetastoreTableOperations failure
    wraps the root NoSuchTableException-style cause in several thousand chars of Catalyst
    analyzer frames on *both* sides, so neither the head window nor the (default 500-char)
    tail window created by the 2026-07-28 fix ever reached it. This is the scenario
    test_truncate_error_message_preserves_tail_not_just_head doesn't cover, since that test's
    `Caused by:` line sits right at the very end (naturally inside the tail window)."""
    head_padding = "at org.apache.spark.sql.catalyst.analysis.Analyzer.frame " * 200
    root_cause = "Caused by: org.apache.iceberg.exceptions.NoSuchTableException: real root cause"
    tail_padding = "at py4j.ClientServerConnection.run frame " * 200
    message = f"{head_padding}{root_cause}{tail_padding}"

    # Sanity: the buried line really is outside both the head and tail windows this function
    # keeps, i.e. the bug this test guards against is real before the fix is applied.
    limit, tail = 4000, 500
    assert root_cause not in message[: limit - tail]
    assert root_cause not in message[-tail:]

    result = truncate_error_message(message, limit=limit, tail=tail)

    assert root_cause in result
    assert "truncated" in result


def test_deepest_caused_by_returns_the_last_caused_by_line() -> None:
    message = (
        "Caused by: org.apache.spark.SparkException: outer wrapper\n"
        "\tat some.frame(Foo.scala:1)\n"
        "Caused by: org.apache.iceberg.exceptions.NoSuchTableException: actual root cause\n"
        "\tat some.other.frame(Bar.scala:2)\n"
    )
    assert deepest_caused_by(message) == (
        "Caused by: org.apache.iceberg.exceptions.NoSuchTableException: actual root cause"
    )


def test_deepest_caused_by_none_when_absent() -> None:
    assert deepest_caused_by("OutOfMemoryError: Java heap space") is None
    assert deepest_caused_by(None) is None
    assert deepest_caused_by("") is None


def test_deepest_caused_by_falls_back_to_py4j_wrapped_exception_header() -> None:
    """Regression, PDAE-8346, confirmed live 2026-08-18: not every JVM exception chain uses
    the literal "Caused by:" marker. Py4J wraps a Java exception as
    "py4j.protocol.Py4JJavaError: An error occurred...\\n: <real exception>: <message>" --
    real sample pulled from a live failed job on gold_dbt_pw_bq's parent dataset, an Iceberg
    ValidationException with no "Caused by:" line anywhere in the captured text."""
    message = (
        "Spark-on-K8s Job FAILED: some INFO chatter\n"
        "py4j.protocol.Py4JJavaError: An error occurred while calling o79.sql.\n"
        ": org.apache.iceberg.exceptions.ValidationException: Deleted manifest "
        "s3a://bucket/path/manifest.avro could not be found in the latest snapshot\n"
        "\tat org.apache.iceberg.BaseRewriteManifests.lambda$validateDeletedManifests$7"
        "(BaseRewriteManifests.java:296)"
    )
    result = deepest_caused_by(message)
    assert result is not None
    assert "org.apache.iceberg.exceptions.ValidationException" in result
    assert "Deleted manifest" in result


def test_deepest_caused_by_falls_back_to_bare_qualified_exception_line() -> None:
    """A stack trace that never went through Py4J's wrapper (or was captured starting after
    it) can still have a bare `pkg.pkg.SomeException: message` header line worth front-
    loading, even with no "Caused by:" anywhere."""
    message = (
        "26/08/12 20:36:33 INFO SparkContext: Running Spark version 4.1.2\n"
        "org.apache.spark.sql.catalyst.parser.ParseException: \n"
        "[INVALID_TYPED_LITERAL] The value of the typed literal is invalid\n"
        "\tat org.apache.spark.sql.errors.QueryParsingErrors$.invalidTypedLiteralError"
        "(QueryParsingErrors.scala:100)"
    )
    result = deepest_caused_by(message)
    assert result is not None
    assert "org.apache.spark.sql.catalyst.parser.ParseException" in result


def test_deepest_caused_by_none_for_bare_stack_frames_with_no_exception_identity() -> None:
    """Confirmed live, PDAE-8346: a real sample of 19/47 truncated errors over 14 days had
    NO exception class or message anywhere in the captured text at all -- only bare
    `at pkg.Class.method(File.java:N)` frames, because the real header line had already
    scrolled past whatever tail window was captured (fixed separately by widening
    _read_pod_log_tail's tail_lines -- this function alone can't recover text that was never
    captured). deepest_caused_by must not fabricate a false signal here; None is correct."""
    message = (
        "at org.apache.iceberg.BaseMetastoreTableOperations.current"
        "(BaseMetastoreTableOperations.java:72)\n"
        "\tat org.apache.iceberg.BaseMetastoreCatalog.loadTable(BaseMetastoreCatalog.java:49)\n"
        "\tat org.apache.iceberg.shaded.com.github.benmanes.caffeine.cache.BoundedLocalCache"
        ".lambda$doComputeIfAbsent$14(BoundedLocalCache.java:2406)"
    )
    assert deepest_caused_by(message) is None


def test_deepest_caused_by_prefers_caused_by_over_header_fallback_when_both_present() -> None:
    """The "Caused by:" path is more precise (it's specifically the *root* cause of a chained
    exception) than the generic header fallback, so it must still win when both exist."""
    message = (
        "org.apache.spark.SparkException: outer wrapper\n"
        "Caused by: org.apache.iceberg.exceptions.NoSuchTableException: actual root cause"
    )
    result = deepest_caused_by(message)
    assert result == "Caused by: org.apache.iceberg.exceptions.NoSuchTableException: actual root cause"


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


def test_is_table_not_found_error_matches_missing_metadata_json() -> None:
    """Regression, 2026-08-03: the cold-table native sweep hit dead catalog entries whose
    current metadata.json no longer exists in S3 -- a different signature than
    NoSuchTableException but the same unrecoverable outcome."""
    assert is_table_not_found_error(
        'failed to load table: blob (key "silver/nuggetoverse/feedback_evolving/metadata/'
        '00000-83b9b4a4-e928-445f-a72e-c6baf29bb799.metadata.json") (code=NotFound): '
        "NoSuchKey: The specified key does not exist."
    )


def test_is_table_not_found_error_matches_missing_manifest_file() -> None:
    assert is_table_not_found_error(
        "remove_orphan_files preview: failed to get referenced files: failed to read "
        "manifests for snapshot 354327196169581740: could not open manifest file: "
        "NoSuchKey: The specified key does not exist."
    )


def test_is_table_not_found_error_does_not_match_bare_nosuchkey() -> None:
    """A missing *data* file (phantom-manifest-entry corruption, fixable via repair) must
    not be misclassified as an unrecoverable dead table -- only a missing metadata.json or
    manifest file (the table's own metadata chain) counts."""
    assert not is_table_not_found_error(
        'failed to read data file "silver/x/data/00042.parquet": NoSuchKey: '
        "The specified key does not exist."
    )


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
