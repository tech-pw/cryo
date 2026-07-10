from __future__ import annotations

from datetime import date

from cryo.domain.enums import Mode, OperationType, PartitionTransform
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    PartitionPredicate,
    PartitionPredicateTerm,
)
from cryo.engines.trino.sql import render, render_predicate, session_setup


def _predicate(*terms: PartitionPredicateTerm) -> PartitionPredicate:
    return PartitionPredicate(terms=terms)


def test_trino_renders_day_epoch_date_and_timestamp_ranges() -> None:
    epoch_day = (date(2025, 5, 18) - date(1970, 1, 1)).days

    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm("event_date", PartitionTransform.DAY, epoch_day, "date")
            )
        )
        == "(event_date >= DATE '2025-05-18' AND event_date < DATE '2025-05-19')"
    )

    assert render_predicate(
        _predicate(
            PartitionPredicateTerm("event_ts", PartitionTransform.DAY, epoch_day, "timestamp")
        )
    ) == (
        "(event_ts >= TIMESTAMP '2025-05-18 00:00:00' "
        "AND event_ts < TIMESTAMP '2025-05-19 00:00:00')"
    )


def test_trino_renders_month_year_and_identity_date() -> None:
    raw_month = (2025 - 1970) * 12 + 4
    raw_year = 2025 - 1970

    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm("event_date", PartitionTransform.MONTH, raw_month, "date")
            )
        )
        == "(event_date >= DATE '2025-05-01' AND event_date < DATE '2025-06-01')"
    )

    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm("event_date", PartitionTransform.YEAR, raw_year, "date")
            )
        )
        == "(event_date >= DATE '2025-01-01' AND event_date < DATE '2026-01-01')"
    )

    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm(
                    "event_date", PartitionTransform.IDENTITY, "2025-05-18", "date"
                )
            )
        )
        == "(event_date = DATE '2025-05-18')"
    )

    # Identity date partitions store Iceberg epoch-days (scanner raw values).
    epoch_day = (date(2026, 7, 9) - date(1970, 1, 1)).days
    assert epoch_day == 20643
    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm("event_date", PartitionTransform.IDENTITY, epoch_day, "date")
            )
        )
        == "(event_date = DATE '2026-07-09')"
    )
    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm(
                    "event_date", PartitionTransform.IDENTITY, str(epoch_day), "date"
                )
            )
        )
        == "(event_date = DATE '2026-07-09')"
    )
    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm(
                    "event_ts", PartitionTransform.IDENTITY, epoch_day, "timestamp"
                )
            )
        )
        == "(event_ts = TIMESTAMP '2026-07-09 00:00:00')"
    )
    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm(
                    "event_ts", PartitionTransform.IDENTITY, str(epoch_day), "timestamp"
                )
            )
        )
        == "(event_ts = TIMESTAMP '2026-07-09 00:00:00')"
    )


def test_trino_render_golden_operation_sql_and_session_setup() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 14, "retain_last": 3}),
            Operation(
                OperationType.OPTIMIZE,
                {"file_size_mb": 256},
                (
                    _predicate(
                        PartitionPredicateTerm(
                            "event_date", PartitionTransform.DAY, "2025-05-18", "date"
                        ),
                        PartitionPredicateTerm(
                            "region", PartitionTransform.IDENTITY, "us", "string"
                        ),
                    ),
                ),
            ),
        ),
    )

    assert [statement.sql for statement in render(plan)] == [
        (
            "ALTER TABLE iceberg.demo.events EXECUTE expire_snapshots("
            "retention_threshold => '14d', retain_last => 3)"
        ),
        (
            "ALTER TABLE iceberg.demo.events EXECUTE optimize(file_size_threshold => '256MB') "
            "WHERE (event_date >= DATE '2025-05-18' AND event_date < DATE '2025-05-19' "
            "AND region = 'us')"
        ),
    ]
    assert session_setup() == [
        "SET SESSION task_min_writer_count = 8",
        "SET SESSION writer_scaling_min_data_processed = '32MB'",
    ]


def test_trino_renders_compute_statistics_all_columns() -> None:
    """ANALYZE TABLE without specific columns — refresh NDV stats for all columns."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.DEEP,
        operations=(Operation(OperationType.COMPUTE_STATISTICS, {"columns": []}),),
    )
    stmts = render(plan)
    assert len(stmts) == 1
    assert stmts[0].sql == "ANALYZE iceberg.demo.events"


def test_trino_renders_compute_statistics_specific_columns() -> None:
    """ANALYZE TABLE with column list — targeted NDV refresh."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.COMPUTE_STATISTICS, {"columns": ["user_id", "event_date"]}),
        ),
    )
    stmts = render(plan)
    assert len(stmts) == 1
    assert stmts[0].sql == (
        "ANALYZE iceberg.demo.events WITH (columns = ARRAY['user_id', 'event_date'])"
    )


def test_trino_convert_deletes_reuses_optimize_sql() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.CONVERT_DELETES, {"file_size_mb": 128}),),
    )
    stmts = render(plan)
    assert len(stmts) == 1
    assert stmts[0].sql == (
        "ALTER TABLE iceberg.demo.events EXECUTE optimize(file_size_threshold => '128MB')"
    )
