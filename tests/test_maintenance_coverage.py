"""Tests for cryo.reporting.maintenance_coverage.measure_cohort_coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import AuditLog, Destination, Preset, Source, Table, TableAssignment
from cryo.domain.enums import OperationType
from cryo.reporting.maintenance_coverage import CORE_OPERATIONS, measure_cohort_coverage

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
PRESET_NAME = "daily-trino-hot-tables"


def _seed_preset(session) -> Preset:
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="spark_emr", config={})
    session.add(destination)
    session.flush()
    preset = Preset(
        id=uuid4(),
        name=PRESET_NAME,
        destination_id=destination.id,
        mode="deep",
        operations=[operation.value for operation in CORE_OPERATIONS],
        params={},
        guardrails={},
        enabled=True,
    )
    session.add(preset)
    session.flush()
    return preset


def _pin_table(session, preset: Preset, *, name: str) -> Table:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    table = Table(id=uuid4(), source_id=source.id, schema_name="s", table_name=name, is_active=True)
    session.add(table)
    session.flush()
    session.add(TableAssignment(table_id=table.id, preset_id=preset.id, pinned=True))
    session.flush()
    return table


def _audit(session, table: Table, operation: OperationType, status: str, *, ago_hours: int = 1):
    session.add(
        AuditLog(
            ts=NOW - timedelta(hours=ago_hours),
            table_id=table.id,
            action=operation.value,
            status=status,
            engine="spark_emr",
        )
    )


def _coverage_by_op(coverage) -> dict[OperationType, int]:
    return {item.operation: item.covered_tables for item in coverage.operations}


def test_coverage_is_zero_for_an_empty_cohort(session):
    _seed_preset(session)
    session.commit()

    coverage = measure_cohort_coverage(session, preset_name=PRESET_NAME, now=NOW)

    assert coverage.cohort_size == 0
    assert coverage.fully_covered_pct is None
    assert coverage.untouched_tables == 0


def test_a_table_with_all_four_operations_is_fully_covered(session):
    preset = _seed_preset(session)
    table = _pin_table(session, preset, name="hot")
    for operation in CORE_OPERATIONS:
        _audit(session, table, operation, "OK")
    session.commit()

    coverage = measure_cohort_coverage(session, preset_name=PRESET_NAME, now=NOW)

    assert coverage.cohort_size == 1
    assert coverage.fully_covered_pct == 100.0
    assert coverage.untouched_tables == 0


def test_a_skipped_operation_still_counts_as_covered(session):
    preset = _seed_preset(session)
    table = _pin_table(session, preset, name="quiet")
    _audit(session, table, OperationType.OPTIMIZE, "SKIPPED")
    for operation in CORE_OPERATIONS[1:]:
        _audit(session, table, operation, "OK")
    session.commit()

    coverage = measure_cohort_coverage(session, preset_name=PRESET_NAME, now=NOW)

    assert coverage.fully_covered_pct == 100.0


def test_a_partially_maintained_table_shows_the_missing_operation(session):
    preset = _seed_preset(session)
    table = _pin_table(session, preset, name="partial")
    _audit(session, table, OperationType.OPTIMIZE, "OK")
    _audit(session, table, OperationType.EXPIRE_SNAPSHOTS, "ERROR")
    session.commit()

    coverage = measure_cohort_coverage(session, preset_name=PRESET_NAME, now=NOW)
    by_op = _coverage_by_op(coverage)

    assert by_op[OperationType.OPTIMIZE] == 1
    assert by_op[OperationType.EXPIRE_SNAPSHOTS] == 0
    assert by_op[OperationType.OPTIMIZE_MANIFESTS] == 0
    # It was touched, just not completely -- that is a different failure from never running.
    assert coverage.untouched_tables == 0
    assert coverage.fully_covered_pct == 0.0


def test_a_table_maintenance_never_reached_is_counted_as_untouched(session):
    preset = _seed_preset(session)
    _pin_table(session, preset, name="reached")
    missed = _pin_table(session, preset, name="missed")
    for operation in CORE_OPERATIONS:
        _audit(session, session.get(Table, missed.id), operation, "ERROR")
    session.commit()

    coverage = measure_cohort_coverage(session, preset_name=PRESET_NAME, now=NOW)

    assert coverage.cohort_size == 2
    assert coverage.untouched_tables == 2


def test_audit_rows_outside_the_window_do_not_count(session):
    preset = _seed_preset(session)
    table = _pin_table(session, preset, name="stale")
    for operation in CORE_OPERATIONS:
        _audit(session, table, operation, "OK", ago_hours=48)
    session.commit()

    coverage = measure_cohort_coverage(
        session, preset_name=PRESET_NAME, window_hours=24, now=NOW
    )

    assert coverage.fully_covered_pct == 0.0
    assert coverage.untouched_tables == 1


def test_coverage_pct_reflects_the_weakest_operation(session):
    preset = _seed_preset(session)
    tables = [_pin_table(session, preset, name=f"t{index}") for index in range(4)]
    for table in tables:
        _audit(session, table, OperationType.OPTIMIZE, "OK")
    _audit(session, tables[0], OperationType.OPTIMIZE_MANIFESTS, "OK")
    session.commit()

    coverage = measure_cohort_coverage(session, preset_name=PRESET_NAME, now=NOW)
    by_op = _coverage_by_op(coverage)

    assert by_op[OperationType.OPTIMIZE] == 4
    assert by_op[OperationType.OPTIMIZE_MANIFESTS] == 1
    # remove_orphan_files never ran anywhere, so no table is fully covered.
    assert coverage.fully_covered_pct == 0.0
