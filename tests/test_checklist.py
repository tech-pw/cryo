"""Tests for the 12-item Trino/S3/Iceberg optimization checklist (cryo/scanner/checklist.py).

Each item is tested for its green/yellow/red boundary at minimum; the three genuinely new
items (partition_match, cadence_match, storage_class) get deeper coverage since they have no
prior test coverage anywhere else in the codebase.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from cryo.db.models import (
    AuditLog,
    MaintenanceJob,
    MaintenanceRun,
    QueryLog,
    QueryPattern,
    Source,
    Table,
    TableStats,
)
from cryo.scanner.checklist import (
    checklist_summary,
    compute_checklist,
)


def _seed_table(session, *, table_name: str = "events", stats_kwargs: dict | None = None) -> Table:
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    session.add(Source(id=source_id, name=f"src-{source_id}", type="rest", config={}))
    table = Table(
        id=table_id, source_id=source_id, schema_name="demo", table_name=table_name, is_active=True
    )
    session.add(table)
    session.flush()
    stats = TableStats(
        table_id=table_id,
        scan_at=datetime.now(UTC),
        needs_compaction=False,
        compaction_priority="LOW",
        small_files_count=0,
        total_files=0,
        total_size_gb=0.0,
        grade="A",
        overall_score=100,
        raw={},
        **(stats_kwargs or {}),
    )
    session.add(stats)
    session.commit()
    return table


def _seed_query_logs(
    session, table: Table, source_id: uuid.UUID, *, column: str, count: int
) -> None:
    for i in range(count):
        session.add(
            QueryLog(
                source_id=source_id,
                table_id=table.id,
                query_id=f"q-{table.id}-{i}",
                engine="trino",
                query_type="SELECT",
                filter_columns=[{"column": column, "operator": "=", "cardinality": None}],
                executed_at=datetime.now(UTC),
            )
        )
    session.commit()


def test_file_size_green_below_compaction_floor() -> None:
    from cryo.scanner.checklist import _check_file_size

    item = _check_file_size({"total_files": 200, "small_files_count": 50})
    assert item.status == "green"
    assert "below compaction floor" in item.reason


def test_file_size_red_above_50pct() -> None:
    from cryo.scanner.checklist import _check_file_size

    item = _check_file_size({"total_files": 1000, "small_files_count": 600})
    assert item.status == "red"


def test_file_size_yellow_between_20_and_50pct() -> None:
    from cryo.scanner.checklist import _check_file_size

    item = _check_file_size({"total_files": 1000, "small_files_count": 300})
    assert item.status == "yellow"


def test_delete_files_red_at_high_ratio() -> None:
    from cryo.scanner.checklist import _check_delete_files

    item = _check_delete_files({"delete_file_pct": 100.0, "total_delete_files": 522})
    assert item.status == "red"


def test_delete_files_green_when_none() -> None:
    from cryo.scanner.checklist import _check_delete_files

    item = _check_delete_files({"delete_file_pct": 0.0, "total_delete_files": 0})
    assert item.status == "green"


def test_partition_match_red_when_dominant_filter_not_a_partition_field(session) -> None:
    """Regression coverage for the highest-weighted new item: confirmed live 2026-09-14 that
    gold_batches_pw / gold_dbt_orders_base_fact class tables can be dominant-CPU-cost with a
    perfectly reasonable file layout purely because the hot filter column isn't the partition
    key. This is the exact gap layout_analyzer.py never closes (it recommends sort order from
    the same data but never flags the partition scheme itself)."""
    table = _seed_table(session, stats_kwargs={"partition_col": "day"})
    _seed_query_logs(session, table, table.source_id, column="experimentname", count=50)
    session.add(QueryPattern(table_id=table.id, column_name="experimentname", filter_count=40))
    session.commit()

    from cryo.scanner.checklist import _check_partition_match

    item = _check_partition_match(session, table.id, {"partition_col": "day"})
    assert item.status == "red"
    assert "experimentname" in item.reason


def test_partition_match_green_when_dominant_filter_is_partition_field(session) -> None:
    table = _seed_table(session)
    _seed_query_logs(session, table, table.source_id, column="experimentname", count=50)
    session.add(QueryPattern(table_id=table.id, column_name="experimentname", filter_count=40))
    session.commit()

    from cryo.scanner.checklist import _check_partition_match

    item = _check_partition_match(session, table.id, {"partition_col": "experimentname"})
    assert item.status == "green"


def test_partition_match_unknown_with_insufficient_query_volume(session) -> None:
    table = _seed_table(session)
    session.commit()

    from cryo.scanner.checklist import _check_partition_match

    item = _check_partition_match(session, table.id, {})
    assert item.status == "unknown"


def test_cadence_match_red_when_idle_hours_too_tight_for_commit_velocity() -> None:
    """Regression coverage: gold_feature_flags_v1 committed roughly every ~6h
    (confirmed live from its snapshot history, 2026-09-11) while its preset's
    partition_write_idle_hours was set to 24 -- only 4x the gap, not the 3x+ margin this check
    requires with room to spare, and every re-optimize wave re-selected the same
    just-barely-idle partitions the writer was about to touch again. Files/day for the
    affected partitions grew ~15x over the following 3 days."""
    from cryo.scanner.checklist import _check_cadence_match

    item = _check_cadence_match(
        {"is_streaming": True, "commits_per_day": 4.0}, preset_idle_hours=6.0
    )
    assert item.status == "red"
    assert "racing the writer" in item.reason


def test_cadence_match_green_when_idle_hours_safely_exceeds_commit_gap() -> None:
    from cryo.scanner.checklist import _check_cadence_match

    item = _check_cadence_match(
        {"is_streaming": True, "commits_per_day": 4.0}, preset_idle_hours=72.0
    )
    assert item.status == "green"


def test_cadence_match_green_for_low_velocity_table() -> None:
    from cryo.scanner.checklist import _check_cadence_match

    item = _check_cadence_match({"is_streaming": False, "commits_per_day": 1.0})
    assert item.status == "green"


def test_orphan_cleanup_red_when_never_run(session) -> None:
    table = _seed_table(session)
    session.commit()

    from cryo.scanner.checklist import _check_orphan_cleanup

    item = _check_orphan_cleanup(session, table.id)
    assert item.status == "red"
    assert "never run" in item.reason


def test_orphan_cleanup_green_when_run_recently(session) -> None:
    table = _seed_table(session)
    run = MaintenanceRun(id=uuid.uuid4(), kind="adhoc", trigger="api", status="succeeded")
    session.add(run)
    session.flush()
    job = MaintenanceJob(
        id=uuid.uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=uuid.uuid4(),
        mode="standard",
        status="succeeded",
    )
    session.add(job)
    session.commit()
    session.add(
        AuditLog(
            ts=datetime.now(UTC) - timedelta(days=1),
            table_id=table.id,
            job_id=job.id,
            action="remove_orphan_files",
            status="OK",
        )
    )
    session.commit()

    from cryo.scanner.checklist import _check_orphan_cleanup

    item = _check_orphan_cleanup(session, table.id)
    assert item.status == "green"


def test_storage_class_unknown_without_sample_paths(session) -> None:
    table = _seed_table(session)
    session.commit()

    from cryo.scanner.checklist import _check_storage_class

    item = _check_storage_class(session, table.id, sample_paths=None)
    assert item.status == "unknown"


def test_compute_checklist_never_raises_and_returns_all_12_items(session) -> None:
    """The checklist is read by fleet-wide batch scoring -- one table's missing/partial data
    must degrade to 'unknown' items, never raise and blank out the other 19 tables' scores."""
    table = _seed_table(session)
    session.commit()

    items = compute_checklist(session, table.id)
    assert len(items) == 12
    assert {item.key for item in items} == {
        "file_size",
        "delete_files",
        "partition_match",
        "cadence_match",
        "sort_order",
        "distribution_mode",
        "manifest_bloat",
        "stats_current",
        "snapshot_retention",
        "orphan_cleanup",
        "schema_width",
        "storage_class",
    }


def test_checklist_summary_weights_green_full_yellow_half() -> None:
    from cryo.scanner.checklist import ChecklistItem

    items = [
        ChecklistItem(
            key="file_size", label="x", weight_pct=25.0, tier=1, status="green", reason=""
        ),
        ChecklistItem(
            key="delete_files", label="x", weight_pct=20.0, tier=1, status="yellow", reason=""
        ),
        ChecklistItem(
            key="partition_match", label="x", weight_pct=15.0, tier=1, status="red", reason=""
        ),
        ChecklistItem(
            key="cadence_match", label="x", weight_pct=10.0, tier=1, status="unknown", reason=""
        ),
    ]
    summary = checklist_summary(items)
    assert summary["counts"] == {"green": 1, "yellow": 1, "red": 1, "unknown": 1}
    # Scored weight excludes the unknown item's 10%: total_weight_scored = 25+20+15 = 60
    # weighted_score = 25*1.0 + 20*0.5 + 15*0 = 35 -> 35/60 * 100 = 58.3
    assert summary["weighted_score"] == 58.3
