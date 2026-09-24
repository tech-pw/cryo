from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import Source, Table, TableStats
from cryo.telemetry.parser import extract_query_ranges
from cryo.telemetry.patterns import _refresh_wair_query_ranges


def test_extract_query_ranges_combines_two_sided_predicate() -> None:
    ranges = extract_query_ranges(
        "SELECT * FROM analytics.events "
        "WHERE event_date >= DATE '2026-07-01' AND event_date < DATE '2026-08-01'"
    )

    assert [(item.column, item.lower, item.upper) for item in ranges] == [
        ("event_date", "2026-07-01", "2026-08-01")
    ]


def test_extract_query_ranges_supports_between_and_reversed_comparison() -> None:
    between = extract_query_ranges("SELECT * FROM t WHERE amount BETWEEN 10 AND 20")
    reversed_range = extract_query_ranges("SELECT * FROM t WHERE 10 < amount AND 20 >= amount")

    assert [(item.column, item.lower, item.upper) for item in between] == [("amount", 10, 20)]
    assert [(item.column, item.lower, item.upper) for item in reversed_range] == [
        ("amount", 10, 20)
    ]


def test_extract_query_ranges_omits_one_sided_predicates() -> None:
    assert extract_query_ranges("SELECT * FROM t WHERE amount > 10") == []


def test_refresh_wair_ranges_persists_frequency_ranked_evidence(session: Session) -> None:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=f"events_{uuid4().hex}",
        is_active=True,
    )
    stats = TableStats(
        table_id=table.id,
        scan_at=datetime.now(UTC),
        needs_compaction=False,
        compaction_priority="LOW",
        grade="B",
        overall_score=85,
        total_files=10,
        small_files_count=0,
        total_size_gb=1.0,
        raw={"wair_file_sample": []},
    )
    session.add_all([source, table, stats])
    session.commit()
    day = datetime(2026, 9, 20, 10, tzinfo=UTC)
    logs = [
        SimpleNamespace(
            table_id=table.id,
            executed_at=day,
            duration_ms=500,
            bytes_read=1000,
            raw={"query_ranges": [{"column": "amount", "lower": 10, "upper": 20}]},
        ),
        SimpleNamespace(
            table_id=table.id,
            executed_at=day,
            duration_ms=None,
            bytes_read=None,
            raw={"query_ranges": [{"column": "amount", "lower": 10, "upper": 20}]},
        ),
    ]

    _refresh_wair_query_ranges(session, logs)  # type: ignore[arg-type]

    session.refresh(stats)
    assert stats.raw is not None
    assert stats.raw["wair_query_ranges"] == [
        {
            "column": "amount",
            "lower": 10,
            "upper": 20,
            "days": {"2026-09-20": {"queries": 2, "cpu_ms": 500.0, "input_bytes": 1000}},
            "frequency": 2,
            "cpu_ms": 500.0,
            "input_bytes": 1000,
        }
    ]


def test_merge_range_window_drops_days_outside_the_window() -> None:
    from datetime import date

    from cryo.optimizer.wair.ranges import merge_range_window

    first = merge_range_window(
        None,
        {("v", 1, 2): {"queries": 3, "cpu_ms": 30.0, "input_bytes": 300}},
        day=date(2026, 9, 1),
    )
    later = merge_range_window(
        first,
        {("v", 5, None): {"queries": 1, "cpu_ms": 0.0, "input_bytes": 0}},
        day=date(2026, 9, 8),
    )

    assert [(r["lower"], r["upper"], r["frequency"]) for r in later] == [(5, None, 1)]


def test_wair_sample_columns_keeps_only_ranged_numeric_or_temporal_columns() -> None:
    from cryo.optimizer.wair.ranges import wair_sample_columns

    raw = {
        "column_quality": [
            {"column_name": "event_date", "column_type": "date"},
            {"column_name": "email", "column_type": "string"},
            {"column_name": "amount", "column_type": "long"},
        ],
        "wair_query_ranges": [
            {"column": "email", "frequency": 50},
            {"column": "amount", "frequency": 3},
            {"column": "event_date", "frequency": 9},
            {"column": "untyped", "frequency": 99},
        ],
    }

    assert wair_sample_columns(raw) == ["event_date", "amount"]


def test_open_ended_extraction_keeps_one_sided_ranges() -> None:
    ranges = extract_query_ranges("SELECT * FROM t WHERE amount > 10", open_ended=True)

    assert [(r.column, r.lower, r.upper) for r in ranges] == [("amount", 10, None)]
