from datetime import UTC, datetime

from cryo.api.dashboard_routes import (
    aggregate_job_file_counts,
    collect_job_statements,
    format_partition_predicate,
    job_duration_seconds,
    operation_label_from_plan,
    statements_from_detail,
)
from cryo.db.models import AuditLog, JobStep


def test_plain_sql_detail_is_a_statement() -> None:
    assert statements_from_detail("ALTER TABLE t EXECUTE optimize") == [
        "ALTER TABLE t EXECUTE optimize"
    ]
    assert statements_from_detail("NATIVE: rewrite data files") == ["NATIVE: rewrite data files"]
    assert statements_from_detail('{"sql_statements": ["SELECT 1"]}') == ["SELECT 1"]
    assert statements_from_detail("table_locked") == []


def test_collect_job_statements_keeps_every_op_and_skips_force_admit() -> None:
    audits = [
        AuditLog(action="optimize", status="succeeded", detail="ALTER TABLE t EXECUTE optimize"),
        AuditLog(action="force_admit", status="succeeded", detail="ALTER TABLE ignored"),
        AuditLog(
            action="expire_snapshots",
            status="succeeded",
            detail="ALTER TABLE t EXECUTE expire_snapshots",
        ),
    ]
    steps = [JobStep(job_id=audits[0].job_id, step_index=0, operation_type="optimize", detail=None)]
    assert collect_job_statements(audits, steps) == [
        "ALTER TABLE t EXECUTE optimize",
        "ALTER TABLE t EXECUTE expire_snapshots",
    ]


def test_file_counts_sum_measured_ops_and_duration_uses_job_clock() -> None:
    audits = [
        AuditLog(action="optimize", status="succeeded", input_files_count=10, output_files_count=2),
        AuditLog(
            action="expire_snapshots",
            status="succeeded",
            input_files_count=None,
            output_files_count=None,
        ),
        AuditLog(
            action="force_admit", status="succeeded", input_files_count=99, output_files_count=99
        ),
    ]
    assert aggregate_job_file_counts(audits) == (10, 2, 5.0)
    started = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)
    finished = datetime(2026, 10, 6, 1, 2, tzinfo=UTC)
    assert job_duration_seconds(started, finished) == 120


def test_predicates_and_operation_label_are_readable() -> None:
    plan = {
        "operations": [
            {
                "type": "optimize",
                "predicates": [
                    {
                        "unsupported_reason": None,
                        "terms": [
                            {"base_column": "event_date", "transform": "day", "value": 19000}
                        ],
                    }
                ],
            },
            {"type": "expire_snapshots", "predicates": None},
        ]
    }
    assert (
        format_partition_predicate(plan["operations"][0]["predicates"][0])
        == "day(event_date) = 19000"
    )
    assert operation_label_from_plan(plan) == "optimize, expire_snapshots"
