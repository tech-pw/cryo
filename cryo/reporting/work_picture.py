"""What Cryo actually did, separate from whether the jobs failed.

The old north star was "GB freed yesterday." That number is blank whenever the
engine succeeds without reporting sizes, which is exactly how a smaller fleet
can look healthy. This picture keeps three facts apart:

- tables maintained, versus the median of the prior seven full days
- measured bytes, only from actions that reported a size
- measurement coverage of the actions that can report a size

expire_snapshots is listed, but Iceberg's procedure returns file counts, not
sizes, so a missing expire byte total is not treated as a Cryo failure.
fast_path.missing_target_file_size is a finding, not reclaim, and is omitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog
from cryo.reporting.impact_signals import _SUCCESS_STATUSES, day_bounds

MAINTENANCE_ACTIONS = (
    "expire_snapshots",
    "remove_orphan_files",
    "rewrite_position_deletes",
    "optimize",
    "optimize_manifests",
    "convert_deletes",
)
BYTE_ACTIONS = (
    "remove_orphan_files",
    "rewrite_position_deletes",
    "optimize",
    "convert_deletes",
)
_BASELINE_MIN_TABLES = 200
_COVERAGE_WARN = 0.75
_COVERAGE_SEVERE = 0.50
_MEASUREMENT_MIN_ACTIONS = 20
_MEASUREMENT_WARN = 0.50
_COVERAGE_WARN_DEDUCTION = 8.0
_COVERAGE_SEVERE_DEDUCTION = 12.0
_MEASUREMENT_DEDUCTION = 8.0


@dataclass(frozen=True)
class ActionPicture:
    action: str
    succeeded: int
    failed: int
    measured: int
    bytes_freed: int


@dataclass(frozen=True)
class WorkPicture:
    day: date
    tables_maintained: int
    tables_baseline_median: int | None
    byte_actions_succeeded: int
    byte_actions_measured: int
    measured_bytes: int
    by_action: tuple[ActionPicture, ...]
    by_engine: tuple[tuple[str, int], ...]

    @property
    def coverage_ratio(self) -> float | None:
        if not self.tables_baseline_median:
            return None
        return self.tables_maintained / self.tables_baseline_median

    @property
    def measurement_ratio(self) -> float | None:
        if self.byte_actions_succeeded == 0:
            return None
        return self.byte_actions_measured / self.byte_actions_succeeded

    @property
    def measured_gib(self) -> float:
        return self.measured_bytes / (1024**3)


def compute_work_picture(session: Session, *, now: datetime) -> WorkPicture:
    yesterday_start, yesterday_end = day_bounds(now, 1)
    by_action, by_engine, tables = _day_picture(session, yesterday_start, yesterday_end)
    baseline = [_tables_maintained(session, *day_bounds(now, days_ago)) for days_ago in range(2, 9)]
    byte_succeeded = sum(row.succeeded for row in by_action if row.action in BYTE_ACTIONS)
    byte_measured = sum(row.measured for row in by_action if row.action in BYTE_ACTIONS)
    measured_bytes = sum(row.bytes_freed for row in by_action if row.action in BYTE_ACTIONS)
    return WorkPicture(
        day=yesterday_start.date(),
        tables_maintained=tables,
        tables_baseline_median=_median(baseline),
        byte_actions_succeeded=byte_succeeded,
        byte_actions_measured=byte_measured,
        measured_bytes=measured_bytes,
        by_action=tuple(by_action),
        by_engine=tuple(by_engine),
    )


@dataclass(frozen=True)
class WorkDeduction:
    reason: str
    points: float
    severe: bool


def work_picture_deductions(picture: WorkPicture) -> list[WorkDeduction]:
    found: list[WorkDeduction] = []
    baseline = picture.tables_baseline_median or 0
    ratio = picture.coverage_ratio
    if baseline >= _BASELINE_MIN_TABLES and ratio is not None and ratio < _COVERAGE_SEVERE:
        found.append(
            WorkDeduction(
                reason=(
                    f"coverage: {picture.tables_maintained} tables maintained vs "
                    f"median {baseline} over the prior 7 days ({ratio:.0%})"
                ),
                points=_COVERAGE_SEVERE_DEDUCTION,
                severe=True,
            )
        )
    elif baseline >= _BASELINE_MIN_TABLES and ratio is not None and ratio < _COVERAGE_WARN:
        found.append(
            WorkDeduction(
                reason=(
                    f"coverage: {picture.tables_maintained} tables maintained vs "
                    f"median {baseline} over the prior 7 days ({ratio:.0%})"
                ),
                points=_COVERAGE_WARN_DEDUCTION,
                severe=False,
            )
        )
    measured = picture.measurement_ratio
    if (
        picture.byte_actions_succeeded >= _MEASUREMENT_MIN_ACTIONS
        and measured is not None
        and measured < _MEASUREMENT_WARN
    ):
        found.append(
            WorkDeduction(
                reason=(
                    f"measurement: {picture.byte_actions_measured} of "
                    f"{picture.byte_actions_succeeded} byte-capable actions reported a size "
                    f"({measured:.0%}). Success without a size is not GB freed."
                ),
                points=_MEASUREMENT_DEDUCTION,
                severe=False,
            )
        )
    return found


def _day_picture(
    session: Session, start: datetime, end: datetime
) -> tuple[list[ActionPicture], list[tuple[str, int]], int]:
    rows = session.execute(
        select(
            AuditLog.action,
            AuditLog.status,
            func.count(),
            func.count(AuditLog.actual_bytes_freed),
            func.coalesce(func.sum(AuditLog.actual_bytes_freed), 0),
        )
        .where(
            AuditLog.ts >= start,
            AuditLog.ts < end,
            AuditLog.action.in_(MAINTENANCE_ACTIONS),
        )
        .group_by(AuditLog.action, AuditLog.status)
    ).all()
    by_action: dict[str, list[int]] = {}
    for action, status, count, measured, freed in rows:
        bucket = by_action.setdefault(action, [0, 0, 0, 0])
        count = int(count or 0)
        if status in _SUCCESS_STATUSES:
            bucket[0] += count
            bucket[2] += int(measured or 0)
            bucket[3] += int(freed or 0)
        elif status == "FAILED":
            bucket[1] += count
    pictures = [
        ActionPicture(
            action=action,
            succeeded=counts[0],
            failed=counts[1],
            measured=counts[2],
            bytes_freed=counts[3],
        )
        for action, counts in sorted(by_action.items())
    ]
    engines = session.execute(
        select(func.coalesce(AuditLog.engine, "unknown"), func.count())
        .where(
            AuditLog.ts >= start,
            AuditLog.ts < end,
            AuditLog.action.in_(MAINTENANCE_ACTIONS),
            AuditLog.status.in_(_SUCCESS_STATUSES),
        )
        .group_by(AuditLog.engine)
    ).all()
    return (
        pictures,
        [(str(engine), int(count)) for engine, count in engines],
        _tables_maintained(session, start, end),
    )


def _tables_maintained(session: Session, start: datetime, end: datetime) -> int:
    return int(
        session.scalar(
            select(func.count(func.distinct(AuditLog.table_id))).where(
                AuditLog.ts >= start,
                AuditLog.ts < end,
                AuditLog.action.in_(MAINTENANCE_ACTIONS),
                AuditLog.status.in_(_SUCCESS_STATUSES),
            )
        )
        or 0
    )


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2
