from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.api.routes import list_tables
from cryo.db.models import (
    AuditLog,
    Destination,
    Preset,
    QueryLog,
    Source,
    Table,
    TableAssignment,
    TableQueryActivity,
    TableStats,
)


def _source(session, *, name: str | None = None, config: dict | None = None) -> Source:
    source = Source(
        id=uuid4(),
        name=name or f"src-{uuid4().hex[:8]}",
        type="hive",
        config=config or {},
    )
    session.add(source)
    session.flush()
    return source


def _table(
    session,
    source: Source,
    *,
    name: str,
    committed_at: datetime | None = None,
) -> Table:
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="finance",
        table_name=name,
        is_active=True,
    )
    session.add(table)
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="C",
            overall_score=70,
            total_files=10,
            small_files_count=1,
            total_size_gb=2.0,
            last_snapshot_committed_at=committed_at,
        )
    )
    session.flush()
    return table


def _pin(session, table: Table, guardrails: dict) -> None:
    destination = Destination(
        id=uuid4(),
        name=f"dest-{uuid4().hex[:8]}",
        engine="trino",
        config={},
    )
    preset = Preset(
        id=uuid4(),
        name=f"preset-{uuid4().hex[:8]}",
        destination_id=destination.id,
        operations=["optimize"],
        params={},
        guardrails=guardrails,
        enabled=True,
    )
    session.add(destination)
    session.add(preset)
    session.flush()
    session.add(
        TableAssignment(
            table_id=table.id,
            preset_id=preset.id,
            pinned=True,
            param_overrides={},
        )
    )
    session.flush()


def _names(result) -> set[str]:
    return {item.table_name for item in result.items}


def test_freshness_breach_excludes_null_commit_and_null_sla(session) -> None:
    now = datetime.now(UTC)
    source = _source(session)
    stale = _table(session, source, name="stale", committed_at=now - timedelta(hours=48))
    fresh = _table(session, source, name="fresh", committed_at=now - timedelta(hours=1))
    no_commit = _table(session, source, name="no_commit", committed_at=None)
    no_sla = _table(session, source, name="no_sla", committed_at=now - timedelta(hours=48))
    unassigned = _table(session, source, name="unassigned", committed_at=now - timedelta(hours=48))
    _pin(session, stale, {"sla_freshness_hours": 24})
    _pin(session, fresh, {"sla_freshness_hours": 24})
    _pin(session, no_commit, {"sla_freshness_hours": 24})
    _pin(session, no_sla, {})
    session.commit()

    result = list_tables(session, freshness_breach=True)

    assert _names(result) == {"stale"}
    assert result.total == 1
    assert result.unknown_count == 3
    assert unassigned.table_name not in _names(result)


def test_maintenance_overdue_excludes_null_sla(session) -> None:
    now = datetime.now(UTC)
    source = _source(session)
    overdue = _table(session, source, name="overdue")
    recent = _table(session, source, name="recent")
    no_sla = _table(session, source, name="no_sla")
    never = _table(session, source, name="never")
    _pin(session, overdue, {"sla_maintenance_days": 7})
    _pin(session, recent, {"sla_maintenance_days": 7})
    _pin(session, no_sla, {})
    _pin(session, never, {"sla_maintenance_days": 7})
    session.add_all(
        [
            AuditLog(
                table_id=overdue.id,
                action="optimize",
                status="OK",
                ts=now - timedelta(days=10),
            ),
            AuditLog(
                table_id=recent.id,
                action="optimize",
                status="OK",
                ts=now - timedelta(days=1),
            ),
            AuditLog(
                table_id=no_sla.id,
                action="optimize",
                status="succeeded",
                ts=now - timedelta(days=10),
            ),
        ]
    )
    session.commit()

    result = list_tables(session, maintenance_overdue=True)

    assert _names(result) == {"overdue"}
    assert result.total == 1
    assert result.unknown_count == 2


def test_p95_breach_excludes_null_sla_and_null_latency(session) -> None:
    now = datetime.now(UTC)
    source = _source(session)
    slow = _table(session, source, name="slow")
    fast = _table(session, source, name="fast")
    no_sla = _table(session, source, name="no_sla")
    no_reads = _table(session, source, name="no_reads")
    _pin(session, slow, {"sla_p95_latency_ms": 5000})
    _pin(session, fast, {"sla_p95_latency_ms": 5000})
    _pin(session, no_sla, {})
    _pin(session, no_reads, {"sla_p95_latency_ms": 5000})
    for table, durations in (
        (slow, (100, 200, 9000)),
        (fast, (50, 80, 120)),
        (no_sla, (100, 200, 9000)),
    ):
        for index, duration in enumerate(durations):
            session.add(
                QueryLog(
                    source_id=source.id,
                    table_id=table.id,
                    query_id=f"{table.table_name}-{index}",
                    engine="trino",
                    executed_at=now - timedelta(hours=2),
                    duration_ms=duration,
                )
            )
    session.commit()

    result = list_tables(session, p95_breach=True)

    assert _names(result) == {"slow"}
    assert result.total == 1
    assert result.unknown_count == 2


def test_no_reads_uses_activity_when_query_logs_empty_and_skips_coverage_gaps(session) -> None:
    now = datetime.now(UTC)
    logged = _source(session, name="logged")
    _table(session, logged, name="quiet_logged")
    read_logged = _table(session, logged, name="read_logged")
    session.add(
        QueryLog(
            source_id=logged.id,
            table_id=read_logged.id,
            query_id="read-1",
            engine="trino",
            executed_at=now - timedelta(hours=3),
            duration_ms=10,
        )
    )

    iceberg = _source(
        session,
        name="iceberg",
        config={"query_log": {"mode": "iceberg_table", "table": "logs.queries"}},
    )
    _table(session, iceberg, name="quiet_activity")
    read_activity = _table(session, iceberg, name="read_activity")
    session.add(
        TableQueryActivity(
            table_id=read_activity.id,
            day=(now - timedelta(days=1)).date(),
            query_count=4,
        )
    )

    gap_source = _source(session, name="gap")
    gap_table = _table(session, gap_source, name="gap_table")
    session.commit()

    result = list_tables(session, no_reads_days=7)

    assert _names(result) == {"quiet_logged", "quiet_activity"}
    assert gap_table.table_name not in _names(result)
    assert result.total == 2
    assert result.telemetry_window_days == 7
    assert result.telemetry_source == "mixed"
    assert result.telemetry_gap_tables == 1
    assert result.telemetry_coverage == 4 / 5
    assert result.unknown_count is None


def test_no_reads_falls_back_to_table_query_activity_when_query_logs_are_empty(session) -> None:
    now = datetime.now(UTC)
    source = _source(session, config={"query_log": {"mode": "iceberg_table"}})
    _table(session, source, name="quiet")
    hot = _table(session, source, name="hot")
    session.add(
        TableQueryActivity(
            table_id=hot.id,
            day=now.date(),
            query_count=2,
        )
    )
    session.commit()

    result = list_tables(session, no_reads_days=30)

    assert _names(result) == {"quiet"}
    assert result.telemetry_source == "table_query_activity"
    assert result.telemetry_coverage == 1.0
    assert result.telemetry_gap_tables == 0


def test_no_reads_coverage_gap_is_not_returned_as_unused(session) -> None:
    source = _source(session)
    _table(session, source, name="silent")
    session.commit()

    result = list_tables(session, no_reads_days=30)

    assert result.items == []
    assert result.total == 0
    assert result.telemetry_source == "none"
    assert result.telemetry_coverage == 0.0
    assert result.telemetry_gap_tables == 1
