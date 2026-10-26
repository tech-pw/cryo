"""Per-table backoff after repeated resource-exhaustion failures (#180).

A Spark optimize that hits its deadline used to be planned again on the next run with the
same deadline. The only failure memory was adaptive routing's per-destination streak, which
is opt-in (``guardrails.adaptive_routing``) and only reroutes when another destination
exists. In one install, 62 optimize steps on 23 tables timed out in 7 days, and one table
timed out 11 times. Each attempt held a Spark slot for the full deadline.

This module arms the table's existing ``maintenance_retry_after`` gate, which both the
cron scheduler (``tables_for_preset``) and the nightly batch selection already respect and
which the readiness view already shows as "Maintenance retry backoff until ...". It applies
whatever the preset's routing setting is.

Only deadline and OOM failures count. Any other failure, or a success, ends the streak.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from cryo.db.models import MaintenanceJob, Table

# Markers the engines put in a job error when the work ran out of time or memory:
# - "did not complete within": the Spark-on-K8s / EMR poll deadline (k8s.py, emr.py).
# - "DeadlineExceeded": Kubernetes killed the Job at activeDeadlineSeconds first.
# - "OOMKilled exit": the kernel killed the driver (k8s_engine.is_spark_driver_oom).
_RESOURCE_EXHAUSTION_MARKERS = ("did not complete within", "DeadlineExceeded", "OOMKilled exit")

# Consecutive resource failures -> backoff. One failure is retried on the next run as
# before; it may have been bad luck (a busy cluster). From the second on, the wait doubles.
_BACKOFF_SCHEDULE: tuple[tuple[int, timedelta], ...] = (
    (5, timedelta(days=7)),
    (4, timedelta(hours=72)),
    (3, timedelta(hours=36)),
    (2, timedelta(hours=18)),
)

_STREAK_LOOKBACK = 10


def is_resource_exhaustion_error(error: str | None) -> bool:
    if not error:
        return False
    return any(marker in error for marker in _RESOURCE_EXHAUSTION_MARKERS)


def backoff_for_streak(streak: int) -> timedelta | None:
    for threshold, wait in _BACKOFF_SCHEDULE:
        if streak >= threshold:
            return wait
    return None


def resource_failure_streak(session: Session, table_id: uuid.UUID) -> int:
    """How many of the table's most recent finished jobs failed on time or memory, in a row.

    PARTIAL counts when its error is a deadline or OOM: an optimize that timed out next to an
    expire that landed is still an optimize that will time out again.
    """
    rows = session.execute(
        select(MaintenanceJob.status, MaintenanceJob.error)
        .where(
            MaintenanceJob.table_id == table_id,
            MaintenanceJob.started_at.is_not(None),
            MaintenanceJob.status.in_(("succeeded", "failed", "partial")),
        )
        .order_by(MaintenanceJob.started_at.desc())
        .limit(_STREAK_LOOKBACK)
    ).all()
    streak = 0
    for status, error in rows:
        if status in ("failed", "partial") and is_resource_exhaustion_error(error):
            streak += 1
            continue
        break
    return streak


def record_resource_failure_backoff(
    session: Session, table_id: uuid.UUID, *, now: datetime | None = None
) -> datetime | None:
    """Arm the table's retry-after when its resource-failure streak calls for it.

    Call after the failed job's own status is written, so the streak includes it. Never
    shortens an existing, later retry-after (for example a missing-metadata containment).
    Returns the new retry-after, or None when nothing changed.
    """
    current = now or datetime.now(UTC)
    wait = backoff_for_streak(resource_failure_streak(session, table_id))
    if wait is None:
        return None
    until = current + wait
    result = session.execute(
        update(Table)
        .where(
            Table.id == table_id,
            or_(Table.maintenance_retry_after.is_(None), Table.maintenance_retry_after < until),
        )
        .values(maintenance_retry_after=until)
    )
    return until if getattr(result, "rowcount", 0) else None


def clear_resource_failure_backoff(
    session: Session, table_id: uuid.UUID, *, now: datetime | None = None
) -> None:
    """A success ends the backoff early (for example a manual run with a bigger deadline).

    Leaves a missing-metadata containment alone: that one has its own reason column and is
    released only by clear_missing_metadata.
    """
    current = now or datetime.now(UTC)
    session.execute(
        update(Table)
        .where(
            Table.id == table_id,
            Table.maintenance_missing_metadata_reason.is_(None),
            Table.maintenance_retry_after.is_not(None),
            Table.maintenance_retry_after > current,
        )
        .values(maintenance_retry_after=None)
    )
