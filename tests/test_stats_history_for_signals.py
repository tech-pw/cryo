"""Regression, 2026-07-27: stats_history_for_signals feeds detect_streaming_from_history a
raw snapshot-count delta with no awareness of Cryo's own maintenance activity. A single deep
run's expire_snapshots/optimize/optimize_manifests steps each commit their own snapshot, so
the run itself inflates total_snapshots between two scans -- confirmed live on
analytics.silver_launch_events: 8 -> 36 snapshots in minutes, misread as 80 commits/day of
streaming writes. stats_history_for_signals must mark the latest row when a Cryo job explains
the delta, so detect_streaming_from_history can suppress the false positive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Table, TableStatsHistory
from cryo.scanner.persistence import stats_history_for_signals


def _seed_table(session) -> object:
    table = Table(
        id=uuid4(),
        source_id=uuid4(),
        schema_name="analytics",
        table_name="silver_launch_events",
        is_active=True,
    )
    session.add(table)
    session.flush()
    return table


def _seed_history(session, table_id, *, scan_at: datetime, total_snapshots: int) -> None:
    session.add(
        TableStatsHistory(
            table_id=table_id,
            scan_at=scan_at,
            raw={"total_snapshots": total_snapshots},
        )
    )


def test_marks_latest_row_when_maintenance_job_explains_the_snapshot_delta(session) -> None:
    table = _seed_table(session)
    previous_at = datetime(2026, 7, 27, 2, 0, tzinfo=UTC)
    latest_at = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    _seed_history(session, table.id, scan_at=previous_at, total_snapshots=8)
    _seed_history(session, table.id, scan_at=latest_at, total_snapshots=36)

    destination = Destination(id=uuid4(), name="dest", engine="native", config={})
    run = MaintenanceRun(kind="adhoc", trigger="web-detail", status="succeeded")
    session.add_all([destination, run])
    session.flush()
    session.add(
        MaintenanceJob(
            run_id=run.id,
            table_id=table.id,
            destination_id=destination.id,
            mode="deep",
            status="succeeded",
            started_at=previous_at + timedelta(hours=1),
            finished_at=previous_at + timedelta(hours=1, minutes=3),
        )
    )
    session.commit()

    history = stats_history_for_signals(session, table.id)

    assert history[-1]["maintenance_job_since_previous"] is True


def test_does_not_mark_latest_row_without_a_maintenance_job(session) -> None:
    table = _seed_table(session)
    previous_at = datetime(2026, 7, 26, 2, 0, tzinfo=UTC)
    latest_at = datetime(2026, 7, 27, 2, 0, tzinfo=UTC)
    _seed_history(session, table.id, scan_at=previous_at, total_snapshots=8)
    _seed_history(session, table.id, scan_at=latest_at, total_snapshots=40)
    session.commit()

    history = stats_history_for_signals(session, table.id)

    assert history[-1]["maintenance_job_since_previous"] is False


def test_ignores_a_maintenance_job_outside_the_window(session) -> None:
    """A job that finished before the previous scan (or after the latest one) doesn't explain
    this window's delta and must not suppress a genuine streaming signal."""
    table = _seed_table(session)
    previous_at = datetime(2026, 7, 27, 2, 0, tzinfo=UTC)
    latest_at = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    _seed_history(session, table.id, scan_at=previous_at, total_snapshots=8)
    _seed_history(session, table.id, scan_at=latest_at, total_snapshots=36)

    destination = Destination(id=uuid4(), name="dest", engine="native", config={})
    run = MaintenanceRun(kind="adhoc", trigger="web-detail", status="succeeded")
    session.add_all([destination, run])
    session.flush()
    session.add(
        MaintenanceJob(
            run_id=run.id,
            table_id=table.id,
            destination_id=destination.id,
            mode="deep",
            status="succeeded",
            started_at=previous_at - timedelta(hours=2),
            finished_at=previous_at - timedelta(hours=1),
        )
    )
    session.commit()

    history = stats_history_for_signals(session, table.id)

    assert history[-1]["maintenance_job_since_previous"] is False
