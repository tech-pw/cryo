from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from cryo.config import Settings, get_settings
from cryo.db.base import Base
from cryo.db.models import QueryLog, Source, Table, TablePartition, TableStats, TableStatsHistory
from cryo.domain.enums import CostPrecision
from cryo.scanner.analyze import PartitionAnalysis, analyze_table
from cryo.scanner.cost import GB, estimate_storage_cost
from cryo.scanner.health import compute_health_score
from cryo.scanner.persistence import (
    _partition_rows,
    persist_table_analysis,
    record_table_scan_outcome,
)
from cryo.scanner.run import _rebuild_cost_analysis, deterministic_chunk, parse_args
from tests.test_presets_api_db import (
    _assign_missing_uuid_primary_keys,
)
from tests.test_presets_api_db import (
    _patch_sqlite_incompatible_defaults as _do_patch_sqlite_incompatible_defaults,
)

MB = 1024 * 1024


def _patch_sqlite_incompatible_defaults() -> None:
    """Thin wrapper preserving this file's existing bare no-restore call sites' call shape
    (`_patch_sqlite_incompatible_defaults()`, no captured return value) while fixing the actual
    leak: _restore_sqlite_defaults below (an autouse fixture) puts the real server_default back
    on teardown regardless of which test in this module called this. Real bug fixed 2026-09-21:
    this file used to call test_presets_api_db.py's helper directly with no restore at all --
    same shared-Base.metadata-mutation leak as that file's own api_db fixture had, corrupting
    later real-Postgres tests' primary-key generation for the rest of the pytest process."""
    global _pending_sqlite_restore
    _pending_sqlite_restore = _do_patch_sqlite_incompatible_defaults()


_pending_sqlite_restore: list[tuple[object, object]] = []


@pytest.fixture(autouse=True)
def _restore_sqlite_defaults():
    global _pending_sqlite_restore
    _pending_sqlite_restore = []
    yield
    for column, original_default in _pending_sqlite_restore:
        column.server_default = original_default
    _pending_sqlite_restore = []


def test_health_score_uses_warn_and_critical_deductions() -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    stats = {
        "total_files": 100,
        "delete_file_pct": 0,
        "total_delete_files": 0,
        "total_snapshots": 55,
        "first_updated_at": now - timedelta(days=8),
        "manifest_count": 0,
        "total_partitions": 0,
        "partition_skew_ratio": 0,
        "avg_files_per_partition": 0,
        "write_distribution_mode": "hash",
        "has_sort_order": True,
        "target_file_size_bytes": 268_435_456,
        "file_formats": ["PARQUET"],
    }

    # health_small_file_count=25 is below MIN_SMALL_FILES_FOR_COMPACTION (128) -- the planner
    # would never schedule OPTIMIZE for this table (see planner.py's min_small_files gate), so
    # the health score must not deduct for small-file percentage here either. Real-fleet
    # evidence for this behavior: /2026-08-05 found 76% of D-graded tables had fewer
    # total files than this floor, and this exact deduction alone accounted for 99.6% of the
    # bucket's unfixable-critical grades.
    score, grade, deductions = compute_health_score(stats, health_small_file_count=25, now=now)

    assert score == 84
    assert grade == "B"
    assert sum(d["deduction"] for d in deductions) == 100 - score
    assert {d["label"] for d in deductions} == {
        "Snapshot retention buildup",
        "Stale table commits",
    }


def test_health_score_still_deducts_small_file_pct_above_compaction_floor() -> None:
    """Same shape as the test above, but with enough small files (>=128) that the planner
    would actually schedule OPTIMIZE for this table -- the percentage deduction must still
    apply here, otherwise the exemption would silently swallow real, fixable fragmentation."""
    now = datetime(2026, 6, 30, tzinfo=UTC)
    stats = {
        "total_files": 500,
        "delete_file_pct": 0,
        "total_delete_files": 0,
        "total_snapshots": 5,
        "first_updated_at": now,
        "manifest_count": 0,
        "total_partitions": 0,
        "partition_skew_ratio": 0,
        "avg_files_per_partition": 0,
        "write_distribution_mode": "hash",
        "has_sort_order": True,
        "target_file_size_bytes": 268_435_456,
        "file_formats": ["PARQUET"],
    }

    score, grade, deductions = compute_health_score(stats, health_small_file_count=130, now=now)

    # 130/500 = 26% -- above the 20% warn threshold, below the 50% critical threshold.
    assert score == 92
    assert {d["label"] for d in deductions} == {"Small-file fragmentation"}


def test_storage_cost_includes_snapshot_overhead() -> None:
    estimate = estimate_storage_cost(
        total_size_bytes=100 * GB,
        total_delete_bytes=5 * GB,
        manifest_total_bytes=1 * GB,
        total_snapshots=50,
    )

    assert estimate.snapshot_overhead_gb == 15
    assert estimate.monthly_s3_cost_usd == 2.78


def test_deterministic_chunk_uses_full_sorted_position() -> None:
    assert deterministic_chunk(["a", "b", "c", "d", "e"], chunk_id=1, num_chunks=2) == [
        "b",
        "d",
    ]


def test_analyze_table_supports_fake_manifest_objects() -> None:
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = FakeTable(
        files=[
            FakeDataFile(10 * MB, 10, ("2026-06-29",)),
            FakeDataFile(20 * MB, 20, ("2026-06-29",)),
            FakeDataFile(30 * MB, 30, ("2026-06-29",)),
            FakeDataFile(256 * MB, 100, ("2026-06-30",)),
        ],
        snapshots=[FakeSnapshotMeta(scan_at - timedelta(days=1))],
    )

    analysis = analyze_table(table, "demo", "events", scan_at=scan_at)

    assert analysis.schema_name == "demo"
    assert analysis.table_name == "events"
    assert analysis.total_files == 4
    assert analysis.total_records == 160
    assert analysis.small_files_count == 3
    assert analysis.needs_compaction is True
    assert analysis.compaction_priority == "HIGH"
    assert analysis.partition_col == "event_date"
    assert analysis.partition_col_type == "date"
    assert analysis.partitions[0].partition_value == "2026-06-29"
    assert analysis.partitions[0].small_files_in_partition_count == 3
    assert analysis.partitions[0].compaction_status == "F"
    assert analysis.raw["partition_fields"][0]["transform"] == "day"


def test_analyze_table_retains_bounded_wair_file_bounds() -> None:
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = FakeTable(
        files=[
            FakeDataFile(
                10 * MB,
                10,
                ("2026-06-29",),
                file_path="s3://bucket/b.parquet",
                lower_bounds={1: "2026-06-20", 2: "m"},
                upper_bounds={1: "2026-06-29", 2: "z"},
            ),
            FakeDataFile(
                20 * MB,
                20,
                ("2026-06-29",),
                file_path="s3://bucket/a.parquet",
                lower_bounds={1: "2026-06-01", 2: "a"},
                upper_bounds={1: "2026-06-10", 2: "l"},
            ),
        ],
        snapshots=[FakeSnapshotMeta(scan_at - timedelta(days=1))],
    )

    analysis = analyze_table(
        table,
        "demo",
        "events",
        scan_at=scan_at,
        settings=Settings(scanner_wair_file_sample_size=1),
        wair_columns=["event_date"],
    )

    assert analysis.raw["wair_file_sample"] == [
        {
            "file_path": "s3://bucket/a.parquet",
            "size_bytes": 20 * MB,
            "lower_bounds": {"event_date": "2026-06-01"},
            "upper_bounds": {"event_date": "2026-06-10"},
        }
    ]
    assert analysis.raw["wair_file_sample_complete"] is False


def test_file_bound_samples_need_range_queried_columns_on_both_scan_paths() -> None:
    # Bounds are only sampled for columns the query log shows being range-filtered.
    from cryo.scanner.go_scan import _build_scan_stats_request

    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = FakeTable(
        files=[
            FakeDataFile(
                10 * MB,
                10,
                ("2026-06-29",),
                file_path="s3://bucket/a.parquet",
                lower_bounds={2: "student-0001"},
                upper_bounds={2: "student-9999"},
            )
        ],
        snapshots=[FakeSnapshotMeta(scan_at - timedelta(days=1))],
    )

    analysis = analyze_table(table, "demo", "events", scan_at=scan_at, settings=Settings())

    assert analysis.raw["wair_file_sample"] == []
    request = _build_scan_stats_request(
        {"uri": "http://catalog:8181", "warehouse": "s3://wh/"},
        schema_name="demo",
        table_name="events",
        settings=Settings(),
    )
    assert request["max_file_samples"] == -1
    assert "sample_columns" not in request
    scoped = _build_scan_stats_request(
        {"uri": "http://catalog:8181", "warehouse": "s3://wh/"},
        schema_name="demo",
        table_name="events",
        settings=Settings(),
        wair_columns=["event_date"],
    )
    assert scoped["max_file_samples"] == 1000
    assert scoped["sample_columns"] == ["event_date"]


def _small_file_analysis(scan_at: datetime):
    table = FakeTable(
        files=[
            FakeDataFile(10 * MB, 10, ("2026-06-29",)),
            FakeDataFile(20 * MB, 20, ("2026-06-29",)),
            FakeDataFile(30 * MB, 30, ("2026-06-29",)),
            FakeDataFile(256 * MB, 100, ("2026-06-30",)),
        ],
        snapshots=[FakeSnapshotMeta(scan_at - timedelta(days=1))],
    )
    return analyze_table(table, "demo", "events", scan_at=scan_at)


def test_rebuild_cost_analysis_defaults_query_count_30d_to_zero() -> None:
    """Documents the function's own safe default -- the real bug this guarded against was the
    *caller* (cryo/scanner/run.py's _apply_orphan_scan) always passing a hardcoded 0 regardless
    of the table's actual traffic, which made query_request_savings_usd_month permanently $0 in
    production. That call site now passes the table's real previous-scan query_count_30d; this
    test just pins the function's own default when a caller omits it entirely."""
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    analysis = _small_file_analysis(scan_at)
    assert analysis.cost is not None

    rebuilt = _rebuild_cost_analysis(
        analysis,
        settings=Settings(),
        orphan_bytes=0,
        listed_bytes=0,
        last_full_cost_scan_at=None,
        precision=CostPrecision.METADATA,
    )

    assert rebuilt.cost is not None
    assert rebuilt.cost.query_request_savings_usd_month == 0.0


def test_rebuild_cost_analysis_uses_real_query_count_30d_when_given() -> None:
    """Regression test for the fix: a nonzero query_count_30d (the table's previous-scan
    telemetry, as now threaded through by _apply_orphan_scan) must actually produce a nonzero
    savings estimate -- previously this was hardcoded to 0 at the call site, so no amount of
    real traffic ever showed up in query_request_savings_usd_month."""
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    analysis = _small_file_analysis(scan_at)
    assert analysis.small_files_count > 0

    rebuilt = _rebuild_cost_analysis(
        analysis,
        settings=Settings(),
        orphan_bytes=0,
        listed_bytes=0,
        last_full_cost_scan_at=None,
        precision=CostPrecision.METADATA,
        query_count_30d=1000,
    )

    assert rebuilt.cost is not None
    assert rebuilt.cost.query_request_savings_usd_month > 0.0


def test_rebuild_cost_analysis_live_price_changes_savings_and_is_tagged() -> None:
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    analysis = _small_file_analysis(scan_at)
    settings = Settings()

    fallback = _rebuild_cost_analysis(
        analysis,
        settings=settings,
        orphan_bytes=0,
        listed_bytes=0,
        last_full_cost_scan_at=None,
        precision=CostPrecision.METADATA,
        query_count_30d=1000,
    )
    live = _rebuild_cost_analysis(
        analysis,
        settings=settings,
        orphan_bytes=0,
        listed_bytes=0,
        last_full_cost_scan_at=None,
        precision=CostPrecision.METADATA,
        query_count_30d=1000,
        live_s3_get_price_per_1k=settings.s3_get_usd_per_1k * 10,
    )

    assert fallback.cost is not None and live.cost is not None
    assert live.cost.query_request_savings_usd_month == round(
        fallback.cost.query_request_savings_usd_month * 10, 4
    )
    assert fallback.cost.raw["assumptions"]["s3_get_price_source"] == "fallback_default"
    assert live.cost.raw["assumptions"]["s3_get_price_source"] == "live"


def test_rebuild_cost_analysis_keeps_raw_cost_in_sync_with_reclaimable_bytes() -> None:
    """Regression, found in production 2026-08-05: this function updated `analysis.cost` (which
    TableStats.reclaimable_bytes gets persisted from) but returned the *original* `analysis.raw`
    unchanged, via `replace(analysis, cost=new_cost)` -- so `raw["cost"]["by_operation"]` (the
    detailed breakdown the UI reads) permanently kept showing the pre-orphan-scan state while the
    scalar total moved on with newly-folded-in orphan bytes. Every table where orphan-scan
    carry-forward applied (i.e. almost every table, since full orphan rescans are infrequent by
    design) ended up with a `reclaimable_bytes` column that silently disagreed with its own row's
    detailed breakdown -- which read as "nothing to reclaim" while the headline number claimed
    otherwise, by up to 129x the table's actual size in the worst production cases."""
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    analysis = _small_file_analysis(scan_at)
    assert analysis.cost is not None

    rebuilt = _rebuild_cost_analysis(
        analysis,
        settings=Settings(),
        orphan_bytes=5_000_000_000,
        listed_bytes=5_000_000_000,
        last_full_cost_scan_at=scan_at,
        precision=CostPrecision.STORAGE_TRUTH,
    )

    assert rebuilt.cost is not None
    assert rebuilt.cost.reclaimable_bytes > 0
    assert rebuilt.raw["cost"] == rebuilt.cost.raw
    persisted_reclaim_total = sum(rebuilt.raw["cost"]["by_operation"].values())
    assert persisted_reclaim_total > 0, (
        "raw['cost']['by_operation'] must reflect the same nonzero reclaim the scalar "
        "reclaimable_bytes column claims -- a UI reading this row's detailed breakdown must "
        "see the same conclusion as the headline number, not a stale, disagreeing one"
    )


def test_analyze_table_computes_zero_scan_column_quality() -> None:
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = FakeTable(
        files=[
            FakeDataFile(
                10 * MB,
                10,
                ("2026-06-29",),
                null_value_counts={2: 0},
                value_counts={2: 50},
            ),
            FakeDataFile(
                20 * MB,
                20,
                ("2026-06-29",),
                null_value_counts={2: 10},
                value_counts={2: 50},
            ),
        ],
        snapshots=[FakeSnapshotMeta(scan_at - timedelta(days=1))],
    )

    analysis = analyze_table(table, "demo", "events", scan_at=scan_at)

    assert analysis.raw["avg_null_rate"] == 0.1
    assert analysis.raw["max_null_rate"] == 0.1
    assert analysis.raw["worst_null_column"] == "user_id"
    [column] = analysis.raw["column_quality"]
    assert column["column_name"] == "user_id"
    assert column["null_count"] == 10
    assert column["value_count"] == 100


def test_persist_table_analysis_adds_latest_partitions_and_history() -> None:
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = Table(id=table_id, source_id=source_id, schema_name="demo", table_name="events")
    analysis = analyze_table(
        FakeTable(
            files=[
                FakeDataFile(10 * MB, 10, ("2026-06-29",)),
                FakeDataFile(20 * MB, 20, ("2026-06-29",)),
            ],
            snapshots=[FakeSnapshotMeta(scan_at)],
        ),
        "demo",
        "events",
        scan_at=scan_at,
    )
    session = FakeSession()

    persisted_id = persist_table_analysis(
        session, source_id=source_id, table=table, analysis=analysis
    )

    assert persisted_id == table_id
    assert any(isinstance(item, TableStats) for item in session.added)
    assert sum(isinstance(item, TablePartition) for item in session.added) == 1
    assert any(isinstance(item, TableStatsHistory) for item in session.added)
    stats = next(item for item in session.added if isinstance(item, TableStats))
    assert stats.total_files == 2
    assert stats.raw["partition_fields"][0]["base_column"] == "event_date"
    assert len(session.executed) == 4


def _fake_partition(small_files: int, size_gb: float = 1.0) -> PartitionAnalysis:
    return PartitionAnalysis(
        partition_value=f"v={small_files}",
        partition_values={"v": small_files},
        partition_fields=[],
        partition_spec_id=0,
        partition_path=None,
        partition_key="v",
        base_column="v",
        base_column_type="int",
        transform=None,
        files_per_partition=small_files + 1,
        small_files_in_partition_count=small_files,
        partition_size_gb=size_gb,
        compaction_status="needs_compaction",
        raw={},
    )


def test_partition_rows_caps_and_keeps_worst_offenders_by_small_files() -> None:
    """Regression test for the table_partitions retention fix: a real production table had
    397k partitions (median table: 8) and alone accounted for ~10% of the table's total row
    count, because every partition from every scan was persisted forever with no cap. Capping
    must keep the partitions that matter for compaction decisions, not an arbitrary slice."""
    table_id = uuid.uuid4()
    partitions = [_fake_partition(small_files=i) for i in range(10)]
    analysis = SimpleNamespace(partitions=partitions, scan_at=datetime(2026, 6, 30, tzinfo=UTC))

    rows = _partition_rows(table_id, analysis, max_partitions=3)

    assert len(rows) == 3
    assert [row.small_files_in_partition_count for row in rows] == [9, 8, 7]


def test_partition_rows_under_cap_keeps_all_partitions() -> None:
    table_id = uuid.uuid4()
    partitions = [_fake_partition(small_files=i) for i in range(3)]
    analysis = SimpleNamespace(partitions=partitions, scan_at=datetime(2026, 6, 30, tzinfo=UTC))

    rows = _partition_rows(table_id, analysis, max_partitions=5000)

    assert len(rows) == 3


def test_persist_table_analysis_strips_nul_bytes_from_history_column_quality() -> None:
    """Regression test: a dirty manifest-decoded column bound containing a NUL byte must not
    reach TableStatsHistory.raw un-sanitized -- Postgres text/JSONB can never store one, and an
    un-guarded INSERT there would fail the whole row (see psycopg UntranslatableCharacter),
    discarding an otherwise-successful scan's real stats. TableStats.raw already went through
    _json_safe; this checks TableStatsHistory.raw gets the same treatment."""
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = Table(id=table_id, source_id=source_id, schema_name="demo", table_name="events")
    analysis = analyze_table(
        FakeTable(
            files=[FakeDataFile(10 * MB, 10, ("2026-06-29",))],
            snapshots=[FakeSnapshotMeta(scan_at)],
        ),
        "demo",
        "events",
        scan_at=scan_at,
    )
    # Simulate a dirty source value decoded straight from a manifest bound.
    analysis.raw["column_quality"] = [
        {"column_name": "notes", "lower_bound": "dirty\x00value", "upper_bound": "clean"}
    ]
    session = FakeSession()

    persist_table_analysis(session, source_id=source_id, table=table, analysis=analysis)

    history = next(item for item in session.added if isinstance(item, TableStatsHistory))
    assert history.raw["column_quality"] == [
        {"column_name": "notes", "lower_bound": "dirtyvalue", "upper_bound": "clean"}
    ]


def test_history_rows_never_copy_the_wair_file_sample() -> None:
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    table = Table(id=table_id, source_id=source_id, schema_name="demo", table_name="events")
    analysis = analyze_table(
        FakeTable(
            files=[FakeDataFile(10 * MB, 10, ("2026-06-29",))],
            snapshots=[FakeSnapshotMeta(scan_at)],
        ),
        "demo",
        "events",
        scan_at=scan_at,
    )
    analysis.raw["wair_file_sample"] = [
        {"file_path": "s3://b/a.parquet", "lower_bounds": {"studentid": "5ecd1fdb"}}
    ]
    session = FakeSession()

    persist_table_analysis(session, source_id=source_id, table=table, analysis=analysis)

    history = next(item for item in session.added if isinstance(item, TableStatsHistory))
    assert not any(key.startswith("wair_") for key in history.raw)


def test_persist_table_analysis_writes_real_p95_query_latency(session) -> None:
    """Real-DB test (not FakeSession, whose .scalar() always returns None) --
    TableStatsHistory.raw["p95_query_latency_ms"] must carry a real value computed from this
    table's own QueryLog rows, previously always absent (see cryo.intelligence.outcomes'
    pre/post_p95_query_latency_ms, which read this exact key and got None for every table)."""
    from cryo.db.models import QueryLog, Source

    source = Source(id=uuid.uuid4(), name="demo-source", type="hive", config={})
    session.add(source)
    session.flush()
    table_id = uuid.uuid4()
    table = Table(id=table_id, source_id=source.id, schema_name="demo", table_name="events")
    session.add(table)
    session.flush()

    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    for i, duration in enumerate([100, 200, 300, 400, 5000]):  # p95 should land near 5000
        session.add(
            QueryLog(
                source_id=source.id,
                table_id=table_id,
                query_id=f"q-{i}",
                engine="trino",
                query_text="SELECT * FROM demo.events",
                executed_at=scan_at - timedelta(hours=i),
                duration_ms=duration,
            )
        )
    session.commit()

    analysis = analyze_table(
        FakeTable(
            files=[FakeDataFile(10 * MB, 10, ("2026-06-29",))],
            snapshots=[FakeSnapshotMeta(scan_at)],
        ),
        "demo",
        "events",
        scan_at=scan_at,
    )

    persist_table_analysis(session, source_id=source.id, table=table, analysis=analysis)
    session.commit()

    history = session.scalars(
        select(TableStatsHistory).where(TableStatsHistory.table_id == table_id)
    ).one()
    assert history.raw["p95_query_latency_ms"] is not None
    assert history.raw["p95_query_latency_ms"] > 400  # clearly above the bulk of durations


def test_persist_table_analysis_p95_query_latency_is_none_with_no_query_log(session) -> None:
    """No QueryLog rows at all for this table -- must be None, not 0.0 (a table genuinely
    never queried has no latency signal to report, which is a different fact than 'queries
    here are instant')."""
    from cryo.db.models import Source

    source = Source(id=uuid.uuid4(), name="demo-source-2", type="hive", config={})
    session.add(source)
    session.flush()
    table_id = uuid.uuid4()
    table = Table(id=table_id, source_id=source.id, schema_name="demo", table_name="quiet_table")
    session.add(table)
    session.flush()

    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    analysis = analyze_table(
        FakeTable(
            files=[FakeDataFile(10 * MB, 10, ("2026-06-29",))],
            snapshots=[FakeSnapshotMeta(scan_at)],
        ),
        "demo",
        "quiet_table",
        scan_at=scan_at,
    )

    persist_table_analysis(session, source_id=source.id, table=table, analysis=analysis)
    session.commit()

    history = session.scalars(
        select(TableStatsHistory).where(TableStatsHistory.table_id == table_id)
    ).one()
    assert history.raw["p95_query_latency_ms"] is None


def test_json_safe_strips_nul_bytes_postgres_cannot_store() -> None:
    """A dirty VARCHAR min/max bound decoded straight from manifest stats can legally contain a
    NUL byte -- Postgres text/JSONB columns can never store one, and would fail the whole row's
    INSERT rather than just that one field (see the psycopg UntranslatableCharacter error this
    guards against)."""
    from cryo.scanner.persistence import _json_safe

    assert _json_safe("clean") == "clean"
    assert _json_safe("dirty\x00value") == "dirtyvalue"
    assert _json_safe({"lower_bound": "\x00leading", "count": 3}) == {
        "lower_bound": "leading",
        "count": 3,
    }
    assert _json_safe(["a\x00b", ("c\x00d",)]) == ["ab", ["cd"]]


def test_json_safe_nulls_out_non_finite_floats_postgres_json_rejects() -> None:
    """A decoded manifest min/max bound for a float/double column can legally be +-Infinity or
    NaN, but Postgres's JSON/JSONB parser rejects those literal tokens outright (see the psycopg
    InvalidTextRepresentation "Token 'Infinity' is invalid" error this guards against)."""
    from cryo.scanner.persistence import _json_safe

    assert _json_safe(1.5) == 1.5
    assert _json_safe(float("inf")) is None
    assert _json_safe(float("-inf")) is None
    assert _json_safe(float("nan")) is None
    assert _json_safe({"lower_bound": 0.999, "upper_bound": float("inf")}) == {
        "lower_bound": 0.999,
        "upper_bound": None,
    }
    assert _json_safe([float("inf"), 2.0]) == [None, 2.0]


def test_record_table_scan_outcome_tracks_and_resets_consecutive_failures() -> None:
    """The whole point of tracking this: a table failing once (a network blip) shouldn't be
    flagged as broken, only a run of repeated failures -- and any later success must clear the
    streak entirely, not just decrement it."""
    _patch_sqlite_incompatible_defaults()
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS cryo"))
        Base.metadata.create_all(connection, tables=[Source.__table__, Table.__table__])
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    event.listen(session_factory, "before_flush", _assign_missing_uuid_primary_keys)

    source_id = uuid.uuid4()
    with session_factory() as session:
        session.add(Source(id=source_id, name="demo", type="rest", config={}))
        session.commit()

    scan_at = datetime(2026, 1, 1, tzinfo=UTC)
    kwargs = {"source_id": source_id, "schema_name": "sch", "table_name": "gold_dbt_tmp"}

    with session_factory() as session:
        table = record_table_scan_outcome(session, **kwargs, error=None, scanned_at=scan_at)
        session.commit()
        assert table.consecutive_scan_failures == 0

    with session_factory() as session:
        table = record_table_scan_outcome(session, **kwargs, error="boom", scanned_at=scan_at)
        session.commit()
        assert table.consecutive_scan_failures == 1
        assert table.last_scan_error == "boom"

    with session_factory() as session:
        table = record_table_scan_outcome(session, **kwargs, error="boom again", scanned_at=scan_at)
        session.commit()
        assert table.consecutive_scan_failures == 2
        assert table.last_scan_error == "boom again"

    with session_factory() as session:
        table = record_table_scan_outcome(session, **kwargs, error=None, scanned_at=scan_at)
        session.commit()
        assert table.consecutive_scan_failures == 0
        assert table.last_scan_error is None
        assert table.last_scan_error_at is None


def test_record_table_scan_outcome_deactivates_dead_table_with_no_query_traffic() -> None:
    """A table past the failure threshold with zero query_logs rows in the lookback window is
    a confirmed-dead catalog entry (stale metadata.json), not a live table Cryo is failing to
    read -- auto-deactivate it so scan_failures/scan_freshness stop flagging it forever."""
    _patch_sqlite_incompatible_defaults()
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS cryo"))
        Base.metadata.create_all(
            connection, tables=[Source.__table__, Table.__table__, QueryLog.__table__]
        )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    event.listen(session_factory, "before_flush", _assign_missing_uuid_primary_keys)

    source_id = uuid.uuid4()
    with session_factory() as session:
        session.add(Source(id=source_id, name="demo", type="rest", config={}))
        session.commit()

    scan_at = datetime(2026, 1, 1, tzinfo=UTC)
    kwargs = {"source_id": source_id, "schema_name": "sch", "table_name": "gold_dbt_tmp"}
    threshold = get_settings().scan_dead_table_failure_threshold

    with session_factory() as session:
        for _ in range(threshold - 1):
            table = record_table_scan_outcome(session, **kwargs, error="boom", scanned_at=scan_at)
            session.commit()
        assert table.is_active is True

        table = record_table_scan_outcome(session, **kwargs, error="boom", scanned_at=scan_at)
        session.commit()
        assert table.consecutive_scan_failures == threshold
        assert table.is_active is False


def test_record_table_scan_outcome_leaves_active_table_with_recent_query_traffic() -> None:
    """A table past the same failure threshold but with a query in the lookback window must
    never be deactivated -- a real user is still reading it, so the failure has some other
    cause and hiding the table would be wrong regardless of the streak length."""
    _patch_sqlite_incompatible_defaults()
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS cryo"))
        Base.metadata.create_all(
            connection, tables=[Source.__table__, Table.__table__, QueryLog.__table__]
        )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    event.listen(session_factory, "before_flush", _assign_missing_uuid_primary_keys)

    source_id = uuid.uuid4()
    with session_factory() as session:
        session.add(Source(id=source_id, name="demo", type="rest", config={}))
        session.commit()

    scan_at = datetime(2026, 1, 1, tzinfo=UTC)
    kwargs = {"source_id": source_id, "schema_name": "sch", "table_name": "still_queried"}
    threshold = get_settings().scan_dead_table_failure_threshold

    with session_factory() as session:
        table = record_table_scan_outcome(session, **kwargs, error="boom", scanned_at=scan_at)
        session.commit()
        session.add(
            QueryLog(
                id=1,
                source_id=source_id,
                table_id=table.id,
                query_id="q-1",
                engine="trino",
                executed_at=scan_at,
            )
        )
        session.commit()

    with session_factory() as session:
        for _ in range(threshold):
            table = record_table_scan_outcome(session, **kwargs, error="boom", scanned_at=scan_at)
            session.commit()
        assert table.consecutive_scan_failures >= threshold
        assert table.is_active is True


def test_upsert_table_survives_concurrent_insert_race() -> None:
    """Regression, 2026-07-30: two scan chunks can discover the same brand-new table at the
    same time -- both SELECT, both find nothing, both try to INSERT. The loser used to hit an
    uncaught IntegrityError on (source_id, schema_name, table_name), which aborted the whole
    session's transaction and killed the *entire* scan chunk (thousands of assigned tables
    abandoned, 0 scanned) instead of just skipping this one already-discovered table. Confirmed
    live: chunk 2 of a fleet scan died this way on demo.analytics.bronze_store_events."""
    from cryo.scanner.persistence import upsert_table

    _patch_sqlite_incompatible_defaults()
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS cryo"))
        Base.metadata.create_all(connection, tables=[Source.__table__, Table.__table__])
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    event.listen(session_factory, "before_flush", _assign_missing_uuid_primary_keys)

    source_id = uuid.uuid4()
    with session_factory() as session:
        session.add(Source(id=source_id, name="demo", type="rest", config={}))
        session.commit()

    # Simulate the "winning" concurrent chunk: its insert is already committed by the time
    # ours runs.
    with session_factory() as session:
        session.add(Table(source_id=source_id, schema_name="analytics", table_name="bronze_x"))
        session.commit()

    # Our chunk raced ahead of that commit: its own initial SELECT (faked here) found nothing,
    # so it proceeds straight to the INSERT, which now genuinely conflicts.
    with session_factory() as session:
        real_scalar = session.scalar
        calls = {"n": 0}

        def _select_misses_once(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return real_scalar(*args, **kwargs)

        session.scalar = _select_misses_once  # type: ignore[method-assign]

        table = upsert_table(
            session, source_id=source_id, schema_name="analytics", table_name="bronze_x"
        )
        assert table is not None
        assert table.schema_name == "analytics"
        assert table.table_name == "bronze_x"
        # The session's transaction must still be usable afterward -- not aborted.
        session.commit()


def test_snapshot_info_tracks_first_and_last_commit() -> None:
    from cryo.scanner.analyze import _snapshot_info

    first = datetime(2026, 1, 1, tzinfo=UTC)
    latest = datetime(2026, 6, 29, tzinfo=UTC)
    table = FakeTable(
        files=[],
        snapshots=[FakeSnapshotMeta(first), FakeSnapshotMeta(latest)],
    )

    info = _snapshot_info(table)

    assert info["first_snapshot_at"] == first
    assert info["last_snapshot_committed_at"] == latest
    assert info["total_snapshots"] == 2


def test_parse_args_accepts_cost_full_scan() -> None:
    args = parse_args(["--cost-full-scan", "--chunk-id", "1", "--num-chunks", "4"])
    assert args.cost_full_scan is True
    assert args.chunk_id == 1
    assert args.num_chunks == 4


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.executed: list[Any] = []

    def scalar(self, statement: Any) -> Any:
        del statement
        return None

    def get(self, model: type[Any], key: Any) -> Any:
        del model, key
        return None

    def add(self, item: Any) -> None:
        self.added.append(item)

    def add_all(self, items: list[Any]) -> None:
        self.added.extend(items)

    def flush(self) -> None:
        for item in self.added:
            if isinstance(item, Table) and item.id is None:
                item.id = uuid.uuid4()

    def execute(self, statement: Any) -> None:
        self.executed.append(statement)


@dataclass(frozen=True)
class FakeSourceColumn:
    name: str
    field_type: str


class FakeSchema:
    _fields = {
        1: FakeSourceColumn("event_date", "date"),
        2: FakeSourceColumn("user_id", "string"),
    }

    def find_field(self, source_id: int) -> FakeSourceColumn:
        return self._fields[source_id]


@dataclass(frozen=True)
class FakePartitionField:
    source_id: int = 1
    name: str = "event_date_day"
    transform: str = "day"


@dataclass(frozen=True)
class FakeSpec:
    fields: list[FakePartitionField]


@dataclass(frozen=True)
class FakeSnapshotMeta:
    committed_at: datetime


@dataclass(frozen=True)
class FakeDataFile:
    file_size_in_bytes: int
    record_count: int
    partition: tuple[Any, ...]
    content: str = "DATA"
    file_format: str = "PARQUET"
    spec_id: int = 0
    null_value_counts: dict[int, int] | None = None
    value_counts: dict[int, int] | None = None
    file_path: str | None = None
    lower_bounds: dict[int, Any] | None = None
    upper_bounds: dict[int, Any] | None = None


@dataclass(frozen=True)
class FakeManifestEntry:
    data_file: FakeDataFile
    status: str = "ADDED"


class FakeManifest:
    manifest_length = 1024

    def __init__(self, files: list[FakeDataFile]) -> None:
        self.files = files

    def fetch_manifest_entry(self, io: object) -> list[FakeManifestEntry]:
        del io
        return [FakeManifestEntry(file) for file in self.files]


class FakeCurrentSnapshot:
    def __init__(self, files: list[FakeDataFile]) -> None:
        self.files = files

    def manifests(self, io: object) -> list[FakeManifest]:
        del io
        return [FakeManifest(self.files)]


class FakeMetadata:
    properties = {
        "write.distribution-mode": "hash",
        "write.target-file-size-bytes": "268435456",
    }
    default_sort_order_id = 1
    sort_orders = [SimpleNamespace(order_id=1, fields=[SimpleNamespace(source_id=1)])]

    def __init__(self, snapshots: list[FakeSnapshotMeta]) -> None:
        self.snapshots = snapshots


class FakeTable:
    io = object()

    def __init__(self, files: list[FakeDataFile], snapshots: list[FakeSnapshotMeta]) -> None:
        self.files = files
        self.metadata = FakeMetadata(snapshots)

    def spec(self) -> FakeSpec:
        return FakeSpec([FakePartitionField()])

    def schema(self) -> FakeSchema:
        return FakeSchema()

    def current_snapshot(self) -> FakeCurrentSnapshot:
        return FakeCurrentSnapshot(self.files)
