from __future__ import annotations

from cryo.domain.enums import Mode, OperationType, PartitionTransform
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    PartitionPredicate,
    PartitionPredicateTerm,
)
from cryo.engines.spark.sql import render, render_predicate


def _predicate(*terms: PartitionPredicateTerm) -> PartitionPredicate:
    return PartitionPredicate(terms=terms)


def test_spark_renders_date_timestamp_month_and_year_predicates() -> None:
    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm("event_date", PartitionTransform.DAY, "2025-05-18", "date")
            )
        )
        == "(event_date >= DATE '2025-05-18' AND event_date < DATE '2025-05-19')"
    )

    assert render_predicate(
        _predicate(
            PartitionPredicateTerm("event_ts", PartitionTransform.DAY, "2025-05-18", "timestamp")
        )
    ) == (
        "(event_ts >= TIMESTAMP '2025-05-18 00:00:00' "
        "AND event_ts < TIMESTAMP '2025-05-19 00:00:00')"
    )

    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm("event_date", PartitionTransform.MONTH, "2025-05", "date")
            )
        )
        == "(event_date >= DATE '2025-05-01' AND event_date < DATE '2025-06-01')"
    )

    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm("event_date", PartitionTransform.YEAR, "2025", "date")
            )
        )
        == "(event_date >= DATE '2025-01-01' AND event_date < DATE '2026-01-01')"
    )


def test_spark_render_golden_operation_sql() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.EXPIRE_SNAPSHOTS,
                {"older_than": "2025-05-01 00:00:00", "retain_last": 3},
            ),
            Operation(
                OperationType.REMOVE_ORPHAN_FILES,
                {"older_than": "2025-05-01 00:00:00"},
            ),
            Operation(
                OperationType.OPTIMIZE,
                {"file_size_mb": 256, "min_input_files": 10},
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
            Operation(OperationType.OPTIMIZE_MANIFESTS, {}),
        ),
    )

    assert [statement.sql for statement in render(plan)] == [
        (
            "CALL iceberg.system.expire_snapshots(table => 'demo.events', "
            "older_than => TIMESTAMP '2025-05-01 00:00:00', retain_last => 3)"
        ),
        (
            "CALL iceberg.system.remove_orphan_files(table => 'demo.events', "
            "older_than => TIMESTAMP '2025-05-01 00:00:00')"
        ),
        (
            "CALL iceberg.system.rewrite_data_files(table => 'demo.events', "
            "where => '(event_date >= DATE ''2025-05-18'' AND event_date < DATE ''2025-05-19'' "
            "AND region = ''us'')', options => map('target-file-size-bytes','268435456', "
            "'min-input-files','10'))"
        ),
        "CALL iceberg.system.rewrite_manifests(table => 'demo.events')",
    ]
