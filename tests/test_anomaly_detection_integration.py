"""Integration tests for anomaly detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Source, Table, TableStatsHistory
from cryo.ml.anomaly_detector import (
    ANOMALY_DETECTION_BATCH_SIZE,
    detect_all_anomalies,
    detect_anomaly_mad,
    detect_table_anomalies,
)


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={},
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


def _seed_history(
    session,
    table: Table,
    *,
    small_files_values: list[int],
    start: datetime | None = None,
) -> None:
    start = start or datetime.now(UTC) - timedelta(days=len(small_files_values))
    for offset, small_files in enumerate(small_files_values):
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=start + timedelta(days=offset),
                small_files_count=small_files,
                total_size_gb=10.0,
                overall_score=80,
            )
        )
    session.commit()


def test_detect_anomaly_mad_flags_outlier():
    """MAD-based detection should flag values far from the median."""
    historical = [100.0, 102.0, 98.0, 101.0, 99.0, 100.0, 103.0]

    is_anomaly, expected, deviation = detect_anomaly_mad(500.0, historical)

    assert is_anomaly is True
    assert expected == 100.0
    assert deviation > 3.5


def test_detect_anomaly_mad_returns_false_for_insufficient_history():
    """MAD detection requires at least five historical points."""
    is_anomaly, expected, deviation = detect_anomaly_mad(500.0, [100.0, 101.0, 99.0])

    assert is_anomaly is False
    assert deviation == 0.0


def test_detect_table_anomalies_finds_small_files_spike(session):
    """detect_table_anomalies should alert on small-file count spikes."""
    table = _seed_table(session, schema_name="test", table_name="anomaly_table")
    _seed_history(
        session,
        table,
        small_files_values=[100, 102, 98, 101, 99, 100, 103, 5000],
    )

    anomalies = detect_table_anomalies(session, table.id, lookback_days=30)

    assert len(anomalies) >= 1
    metric_names = {a.metric_name for a in anomalies}
    assert "small_files_count" in metric_names
    assert anomalies[0].severity in ("low", "medium", "high")


def test_detect_all_anomalies_processes_tables_in_batches(session):
    """detect_all_anomalies should scan recent tables and respect batch sizing."""
    tables = [
        _seed_table(session, schema_name="test", table_name=f"batch_table_{i}")
        for i in range(3)
    ]
    for table in tables:
        _seed_history(
            session,
            table,
            small_files_values=[50, 52, 48, 51, 49, 50, 53, 2000],
            start=datetime.now(UTC) - timedelta(hours=12),
        )

    anomalies = detect_all_anomalies(
        session,
        min_severity="low",
        lookback_days=30,
    )

    assert len(anomalies) >= 1
    table_ids = {a.table_id for a in anomalies}
    assert any(str(t.id) in table_ids for t in tables)


def test_anomaly_detection_batch_size_default():
    """Default batch size constant should match production fallback."""
    assert ANOMALY_DETECTION_BATCH_SIZE == 100
