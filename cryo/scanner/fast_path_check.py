"""Fast-path health check for newly discovered Iceberg tables (RFC Features 2 + 3).

Cheap sweep, independent of the full scanner/analyze pipeline. Flags:

- ``small_files`` — ``FileDegradationSignal`` (40% small files, min 20 files)
- ``missing_target_file_size`` — ``compute_property_recommendations`` when
  ``write.target-file-size-bytes`` is absent (works with ``TableStats`` unset)

Never enqueues optimize jobs. Cooldown uses existing ``AuditLog`` rows keyed by
``table_id`` + ``fast_path.<finding_type>`` (24h), not a new table.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Subquery

from cryo.db.models import AuditLog, Table, TableStatsHistory
from cryo.scanner.property_recommendations import compute_property_recommendations
from cryo.triggers.signals import FileDegradationSignal

NEW_TABLE_WINDOW = timedelta(days=7)
COOLDOWN = timedelta(hours=24)
FAST_PATH_ACTOR = "cryo-fast-path-check"
FINDING_SMALL_FILES = "small_files"
FINDING_MISSING_TARGET = "missing_target_file_size"
TARGET_FILE_SIZE_KEY = "write.target-file-size-bytes"

_SMALL_FILES_SIGNAL = FileDegradationSignal()


def _action_for(finding_type: str) -> str:
    return f"fast_path.{finding_type}"


@dataclass(frozen=True, slots=True)
class FastPathFinding:
    table_id: uuid.UUID
    table_name: str
    finding_type: str
    reason: str
    severity: str
    metric_value: float | None
    detected_at: datetime


def _history_counts_subquery() -> Subquery:
    return (
        select(
            TableStatsHistory.table_id.label("table_id"),
            func.count().label("n"),
        )
        .group_by(TableStatsHistory.table_id)
        .subquery()
    )


def select_new_tables(
    session: Session,
    *,
    now: datetime,
    max_tables: int,
) -> list[Table]:
    """Active tables first seen in the last 7 days and/or with ≤1 stats-history row."""
    cutoff = now - NEW_TABLE_WINDOW
    history = _history_counts_subquery()
    stmt = (
        select(Table)
        .outerjoin(history, history.c.table_id == Table.id)
        .where(Table.is_active.is_(True))
        .where(
            or_(
                Table.first_seen_at >= cutoff,
                func.coalesce(history.c.n, 0) <= 1,
            )
        )
        .order_by(Table.first_seen_at.desc())
        .limit(max_tables)
    )
    return list(session.scalars(stmt))


def _in_cooldown(session: Session, table_id: uuid.UUID, finding_type: str, now: datetime) -> bool:
    since = now - COOLDOWN
    action = _action_for(finding_type)
    existing = session.scalar(
        select(AuditLog.id)
        .where(
            AuditLog.table_id == table_id,
            AuditLog.action == action,
            AuditLog.ts >= since,
        )
        .limit(1)
    )
    return existing is not None


def _persist_finding(session: Session, finding: FastPathFinding) -> None:
    session.add(
        AuditLog(
            table_id=finding.table_id,
            action=_action_for(finding.finding_type),
            status="OK",
            engine=None,
            detail=finding.reason,
            actor=FAST_PATH_ACTOR,
            ts=finding.detected_at,
        )
    )


def _evaluate_table(session: Session, table: Table, now: datetime) -> list[FastPathFinding]:
    table_name = f"{table.schema_name}.{table.table_name}"
    out: list[FastPathFinding] = []

    degradation = _SMALL_FILES_SIGNAL.evaluate(session, table.id)
    if degradation.triggered:
        out.append(
            FastPathFinding(
                table_id=table.id,
                table_name=table_name,
                finding_type=FINDING_SMALL_FILES,
                reason=degradation.reason,
                severity=degradation.severity,
                metric_value=degradation.metric_value,
                detected_at=now,
            )
        )

    recs = compute_property_recommendations(session, table.id)
    missing = next(
        (
            item
            for item in recs
            if item.property_key == TARGET_FILE_SIZE_KEY and item.current_value is None
        ),
        None,
    )
    if missing is not None:
        out.append(
            FastPathFinding(
                table_id=table.id,
                table_name=table_name,
                finding_type=FINDING_MISSING_TARGET,
                reason=missing.reason,
                severity="warning",
                metric_value=None,
                detected_at=now,
            )
        )

    return out


def run_fast_path_check(
    session: Session,
    *,
    max_tables: int = 50,
    now: datetime | None = None,
    persist: bool = True,
    respect_cooldown: bool = True,
) -> list[FastPathFinding]:
    """Sweep new tables; record findings. Does not enqueue compaction."""
    detected_at = now or datetime.now(UTC)
    tables = select_new_tables(session, now=detected_at, max_tables=max_tables)
    findings: list[FastPathFinding] = []

    for table in tables:
        for finding in _evaluate_table(session, table, detected_at):
            if respect_cooldown and _in_cooldown(
                session, finding.table_id, finding.finding_type, detected_at
            ):
                continue
            if persist:
                _persist_finding(session, finding)
            findings.append(finding)

    if persist and findings:
        session.commit()
    return findings


class FastPathTableNotFound(LookupError):
    """The requested table is not in this database. Not a reason to sweep the fleet."""


def load_fast_path_findings(
    session: Session,
    *,
    table_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> list[FastPathFinding]:
    """Fleet sweep when table_id is omitted; one table when it is set.

    The table-detail page must pass table_id. A missing table raises
    FastPathTableNotFound instead of falling through to the fleet sweep.
    """
    detected_at = now or datetime.now(UTC)
    if table_id is not None:
        table = session.get(Table, table_id)
        if table is None:
            raise FastPathTableNotFound(str(table_id))
        return _evaluate_table(session, table, detected_at)
    return run_fast_path_check(session, persist=False, respect_cooldown=False, now=detected_at)


def list_fast_path_findings_from_audit(
    session: Session,
    *,
    now: datetime | None = None,
) -> list[FastPathFinding]:
    """Last-sweep findings still inside the cooldown window (for the API)."""
    detected_at = now or datetime.now(UTC)
    since = detected_at - COOLDOWN
    rows = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.action.in_(
                (_action_for(FINDING_SMALL_FILES), _action_for(FINDING_MISSING_TARGET))
            ),
            AuditLog.ts >= since,
        )
        .order_by(AuditLog.ts.desc())
    )
    out: list[FastPathFinding] = []
    for row in rows:
        if row.table_id is None:
            continue
        table = session.get(Table, row.table_id)
        table_name = (
            f"{table.schema_name}.{table.table_name}" if table is not None else str(row.table_id)
        )
        finding_type = (row.action or "").removeprefix("fast_path.")
        out.append(
            FastPathFinding(
                table_id=row.table_id,
                table_name=table_name,
                finding_type=finding_type,
                reason=row.detail or "",
                severity="warning",
                metric_value=None,
                detected_at=row.ts,
            )
        )
    return out
