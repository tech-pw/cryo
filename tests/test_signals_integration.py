"""Integration tests for maintenance signal detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Source, Table, TableStats, TableStatsHistory
from cryo.signals import detect_all_signals, detect_file_degradation_signal


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={"catalog_name": "iceberg"},
    )
    session.add(source)
    session.flush()
    return source


def _seed_table(session, *, schema_name: str, table_name: str) -> Table:
    source = _seed_source(session)
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name=schema_name,
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.flush()
    return table


def test_detect_file_degradation_signal_triggers_on_spike(session):
    """File degradation signal should fire when small files exceed baseline ratio."""
    table = _seed_table(session, schema_name="test", table_name="degrading_table")
    scan_at = datetime.now(UTC)

    session.add(
        TableStats(
            table_id=table.id,
            scan_at=scan_at,
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="D",
            overall_score=40,
            total_files=5000,
            small_files_count=600,
            total_size_gb=50.0,
        )
    )

    for days_ago in range(7, 0, -1):
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=scan_at - timedelta(days=days_ago),
                small_files_count=100,
                total_size_gb=50.0,
            )
        )
    session.commit()

    signal = detect_file_degradation_signal(
        session,
        table.id,
        lookback_days=7,
        warning_ratio=2.0,
        critical_ratio=3.0,
    )

    assert signal is not None
    assert signal.signal_type == "file_degradation"
    assert signal.severity == "critical"
    assert signal.current_value == 600.0
    assert signal.baseline_value == 100.0


def test_detect_file_degradation_signal_returns_none_when_stable(session):
    """Stable small-file counts should not produce a degradation signal."""
    table = _seed_table(session, schema_name="test", table_name="stable_table")
    scan_at = datetime.now(UTC)

    session.add(
        TableStats(
            table_id=table.id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=95,
            total_files=100,
            small_files_count=10,
            total_size_gb=5.0,
        )
    )
    for days_ago in range(7, 0, -1):
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=scan_at - timedelta(days=days_ago),
                small_files_count=10,
                total_size_gb=5.0,
            )
        )
    session.commit()

    signal = detect_file_degradation_signal(session, table.id)

    assert signal is None


def test_detect_all_signals_collects_per_table_signals(session):
    """detect_all_signals should aggregate signals across active tables."""
    table = _seed_table(session, schema_name="test", table_name="signals_table")
    scan_at = datetime.now(UTC)

    session.add(
        TableStats(
            table_id=table.id,
            scan_at=scan_at,
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="C",
            overall_score=55,
            total_files=3000,
            small_files_count=400,
            total_size_gb=30.0,
        )
    )
    for days_ago in range(7, 0, -1):
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=scan_at - timedelta(days=days_ago),
                small_files_count=80,
                total_size_gb=30.0,
            )
        )
    session.commit()

    signals = detect_all_signals(session, table_id=table.id)

    assert len(signals) >= 1
    assert signals[0].table_id == table.id
    assert signals[0].signal_type == "file_degradation"


def test_detect_all_signals_scans_all_active_tables(session):
    """detect_all_signals without table_id should check every active table."""
    table_a = _seed_table(session, schema_name="test", table_name="signals_a")
    table_b = _seed_table(session, schema_name="test", table_name="signals_b")
    scan_at = datetime.now(UTC)

    for table in (table_a, table_b):
        session.add(
            TableStats(
                table_id=table.id,
                scan_at=scan_at,
                needs_compaction=True,
                compaction_priority="HIGH",
                grade="C",
                overall_score=50,
                total_files=1000,
                small_files_count=300,
                total_size_gb=20.0,
            )
        )
        for days_ago in range(7, 0, -1):
            session.add(
                TableStatsHistory(
                    table_id=table.id,
                    scan_at=scan_at - timedelta(days=days_ago),
                    small_files_count=80,
                    total_size_gb=20.0,
                )
            )
    session.commit()

    signals = detect_all_signals(session)

    signal_table_ids = {s.table_id for s in signals}
    assert table_a.id in signal_table_ids
    assert table_b.id in signal_table_ids
