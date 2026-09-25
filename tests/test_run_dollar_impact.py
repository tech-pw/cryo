"""Tests for cryo/reporting/run_dollar_impact.py's per-table, per-run before/after dollars."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import AuditLog, QueryCostAttributionDaily, QueryLog, Source, Table
from cryo.reporting.run_dollar_impact import (
    compute_run_dollar_impact,
    run_dollar_impact_payload,
)

NOW = datetime(2026, 9, 27, 6, 0, tzinfo=UTC)
RUN_START = NOW - timedelta(hours=2)
RUN_END = NOW


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(session, source: Source, *, name: str = "gold_events") -> Table:
    table = Table(
        id=uuid4(), source_id=source.id, schema_name="finance", table_name=name, is_active=True
    )
    session.add(table)
    session.flush()
    return table


def _audit(
    session,
    table: Table,
    *,
    action: str = "optimize",
    status: str = "OK",
    ts: datetime | None = None,
    input_files: int | None = None,
    output_files: int | None = None,
    bytes_freed: int | None = None,
) -> None:
    session.add(
        AuditLog(
            ts=ts or (RUN_START + timedelta(minutes=30)),
            table_id=table.id,
            action=action,
            status=status,
            input_files_count=input_files,
            output_files_count=output_files,
            actual_bytes_freed=bytes_freed,
        )
    )


def _query_log(
    session,
    source: Source,
    table: Table,
    *,
    executed_at: datetime,
    bytes_read: int,
) -> None:
    session.add(
        QueryLog(
            source_id=source.id,
            table_id=table.id,
            query_id=f"q-{uuid4()}",
            engine="trino",
            executed_at=executed_at,
            bytes_read=bytes_read,
        )
    )


def _price_day(session, source: Source, *, day, bytes_scanned: int, cost_usd: float) -> None:
    session.add(
        QueryCostAttributionDaily(
            source_id=source.id,
            day=day,
            client="unknown",
            schema_name="finance",
            user_name="unknown",
            query_count=10,
            failed_query_count=0,
            physical_bytes_scanned=bytes_scanned,
            estimated_scan_cost_usd=cost_usd,
        )
    )


def test_bytes_freed_and_file_counts_always_computed_without_query_or_price_data(
    session,
) -> None:
    """The files/GB half of the payload must not depend on query-log or price data being
    present -- an evaluation cluster with no query-log ingestion still gets honest before/after
    file counts and bytes freed, just no dollar estimate."""
    source = _seed_source(session)
    table = _seed_table(session, source)
    _audit(session, table, input_files=100, output_files=20, bytes_freed=5 * 1024**3)
    session.flush()

    impact = compute_run_dollar_impact(session, table.id, run_start=RUN_START, run_end=RUN_END)

    assert impact.input_files_count == 100
    assert impact.output_files_count == 20
    assert impact.bytes_freed == 5 * 1024**3
    assert impact.file_count_reduction_ratio == 0.8
    assert impact.estimated_query_cost_delta_usd is None
    assert impact.trino_price_usd_per_gib is None


def test_full_estimate_when_query_log_and_price_both_present(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source)
    _audit(session, table, input_files=100, output_files=25, bytes_freed=8 * 1024**3)

    # 30 queries over the last 30 days -> 1/day, each reading 2 GiB.
    for i in range(30):
        _query_log(
            session,
            source,
            table,
            executed_at=RUN_START - timedelta(days=i, hours=1),
            bytes_read=2 * 1024**3,
        )

    # Priced day: $2 for 1 GiB scanned fleet-wide -> $2.00/GiB.
    _price_day(
        session,
        source,
        day=(RUN_START - timedelta(days=1)).date(),
        bytes_scanned=1024**3,
        cost_usd=2.0,
    )
    session.flush()

    impact = compute_run_dollar_impact(session, table.id, run_start=RUN_START, run_end=RUN_END)

    assert impact.file_count_reduction_ratio == 0.75
    assert impact.queries_per_day_30d == 1.0
    assert impact.avg_bytes_per_query_30d == 2 * 1024**3
    assert impact.trino_price_usd_per_gib == 2.0
    # daily_bytes_scanned = 1 query/day * 2 GiB = 2 GiB -> $4.00/day before.
    assert impact.estimated_daily_query_cost_before_usd == 4.0
    # After: scaled by (1 - 0.75) = 0.25 -> $1.00/day.
    assert impact.estimated_daily_query_cost_after_usd == 1.0
    assert impact.estimated_query_cost_delta_usd == 3.0

    payload = run_dollar_impact_payload(impact)
    assert payload["estimated_query_cost_delta_usd_per_day"] == 3.0
    assert payload["file_count_reduction_pct"] == 75.0
    assert "Not a live before/after query benchmark" in payload["estimate_basis"]


def test_no_price_data_leaves_dollar_fields_none_not_zero(session) -> None:
    """A table with real query-log activity but no priced Trino day yet (cost_ceiling hasn't
    ingested namespace cost) must not report a fabricated $0.00 delta."""
    source = _seed_source(session)
    table = _seed_table(session, source)
    _audit(session, table, input_files=50, output_files=10, bytes_freed=1024**3)
    _query_log(
        session, source, table, executed_at=RUN_START - timedelta(hours=1), bytes_read=1024**3
    )
    session.flush()

    impact = compute_run_dollar_impact(session, table.id, run_start=RUN_START, run_end=RUN_END)

    assert impact.file_count_reduction_ratio == 0.8
    assert impact.trino_price_usd_per_gib is None
    assert impact.estimated_daily_query_cost_before_usd is None
    assert impact.estimated_query_cost_delta_usd is None


def test_no_query_log_activity_leaves_dollar_fields_none(session) -> None:
    """Price data exists fleet-wide, but this specific table has no QueryLog rows -- still no
    fabricated number; a table nobody queries has no honest daily scan-cost estimate."""
    source = _seed_source(session)
    table = _seed_table(session, source)
    _audit(session, table, input_files=50, output_files=10, bytes_freed=1024**3)
    _price_day(
        session,
        source,
        day=(RUN_START - timedelta(days=1)).date(),
        bytes_scanned=1024**3,
        cost_usd=2.0,
    )
    session.flush()

    impact = compute_run_dollar_impact(session, table.id, run_start=RUN_START, run_end=RUN_END)

    assert impact.queries_per_day_30d is None
    assert impact.estimated_query_cost_delta_usd is None


def test_expire_only_run_has_no_file_count_ratio_but_still_reports_bytes(session) -> None:
    """expire_snapshots doesn't report input/output file counts (see _FILE_COUNT_ACTIONS) --
    bytes_freed still comes through, but there's no honest file-count-driven cost delta without
    a before/after file count pair."""
    source = _seed_source(session)
    table = _seed_table(session, source)
    _audit(
        session,
        table,
        action="expire_snapshots",
        input_files=None,
        output_files=None,
        bytes_freed=3 * 1024**3,
    )
    session.flush()

    impact = compute_run_dollar_impact(session, table.id, run_start=RUN_START, run_end=RUN_END)

    assert impact.bytes_freed == 3 * 1024**3
    assert impact.file_count_reduction_ratio is None
    assert impact.estimated_query_cost_delta_usd is None


def test_failed_actions_excluded_from_bytes_and_file_counts(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source)
    _audit(session, table, status="FAILED", input_files=100, output_files=10, bytes_freed=999)
    _audit(session, table, status="OK", input_files=40, output_files=10, bytes_freed=1024**3)
    session.flush()

    impact = compute_run_dollar_impact(session, table.id, run_start=RUN_START, run_end=RUN_END)

    assert impact.bytes_freed == 1024**3
    assert impact.input_files_count == 40
    assert impact.output_files_count == 10


def test_latest_price_prefers_most_recent_priced_day(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source)
    _audit(session, table, input_files=10, output_files=5, bytes_freed=1024**3)
    _query_log(
        session, source, table, executed_at=RUN_START - timedelta(hours=1), bytes_read=1024**3
    )
    # Older, cheaper price.
    _price_day(
        session,
        source,
        day=(RUN_START - timedelta(days=5)).date(),
        bytes_scanned=1024**3,
        cost_usd=1.0,
    )
    # Newer, real price -- must win.
    _price_day(
        session,
        source,
        day=(RUN_START - timedelta(days=1)).date(),
        bytes_scanned=1024**3,
        cost_usd=5.0,
    )
    session.flush()

    impact = compute_run_dollar_impact(session, table.id, run_start=RUN_START, run_end=RUN_END)

    assert impact.trino_price_usd_per_gib == 5.0
