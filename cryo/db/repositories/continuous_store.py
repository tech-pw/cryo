"""DB reads and writes for the continuous-maintenance dispatcher (#77).

Every method takes an explicit, bounded list of table ids: the dispatcher evaluates at most a
handful of tables per tick, never the fleet.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from cryo.db.models import (
    AuditLog,
    MaintenanceJob,
    MaintenanceRun,
    Table,
    TableDrift,
    TableStats,
)
from cryo.db.repositories.exclusions import table_is_excluded
from cryo.db.repositories.presets import (
    COST_LOAD_ONLY_COLUMNS,
    STATS_LOAD_ONLY_COLUMNS,
    table_to_candidate,
)
from cryo.db.repositories.sources import table_in_source_scope
from cryo.scheduler.continuous import (
    CONTINUOUS_TRIGGER_PREFIX,
    DriftView,
    History,
)
from cryo.scheduler.cron import db_instant
from cryo.scheduler.rules import TableCandidate

logger = logging.getLogger(__name__)

# Operations whose success commits a new snapshot. expire_snapshots and remove_orphan_files
# only delete, and compute_statistics writes a statistics file, so they don't add churn.
SNAPSHOT_COMMITTING_ACTIONS = (
    "optimize",
    "convert_deletes",
    "convert_equality_deletes",
    "rewrite_position_deletes",
    "optimize_manifests",
    "expire_data",
)
_RUNS_PER_DAY_SCAN_LIMIT = 2_000


@dataclass(frozen=True)
class ContinuousTableState:
    table_id: str
    drift: DriftView
    history: History
    scan_at: datetime | None


class SQLContinuousStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def drift_table_ids(self, *, limit: int) -> list[str]:
        """Tables with unmaintained writer commits, oldest drift first."""
        with self._session_factory() as session:
            rows = session.scalars(
                select(TableDrift.table_id)
                .where(TableDrift.commits > 0)
                .order_by(TableDrift.first_commit_at.asc().nulls_last())
                .limit(limit)
            ).all()
        return [str(row) for row in rows]

    def list_tables_by_ids(self, table_ids: Sequence[str]) -> list[TableCandidate]:
        ids = _uuids(table_ids)
        if not ids:
            return []
        from cryo.reporting.impact_signals import _RECLAIM_ACTIONS

        with self._session_factory() as session:
            tables = session.scalars(
                select(Table)
                .options(
                    selectinload(Table.stats).load_only(*STATS_LOAD_ONLY_COLUMNS),
                    selectinload(Table.cost).load_only(*COST_LOAD_ONLY_COLUMNS),
                    selectinload(Table.source),
                )
                .where(Table.id.in_(ids), Table.is_active.is_(True), ~table_is_excluded())
            ).all()
            last_maintained: dict[uuid.UUID | None, datetime] = dict(
                session.execute(
                    select(AuditLog.table_id, func.max(AuditLog.ts))
                    .where(
                        AuditLog.table_id.in_(ids),
                        AuditLog.status == "OK",
                        AuditLog.action.in_(_RECLAIM_ACTIONS),
                    )
                    .group_by(AuditLog.table_id)
                )
                .tuples()
                .all()
            )
            return [
                table_to_candidate(table, last_maintained_at=last_maintained.get(table.id))
                for table in tables
                if table.source is not None and table_in_source_scope(table, table.source)
            ]

    def load_state(
        self,
        table_ids: Sequence[str],
        *,
        now: datetime,
        last_maintained: dict[str, datetime | None],
    ) -> dict[str, ContinuousTableState]:
        ids = _uuids(table_ids)
        if not ids:
            return {}
        day_ago = now - timedelta(days=1)
        hour_ago = now - timedelta(hours=1)
        with self._session_factory() as session:
            drifts = {
                row.table_id: row
                for row in session.scalars(select(TableDrift).where(TableDrift.table_id.in_(ids)))
            }
            stats_rows = session.execute(
                select(TableStats.table_id, TableStats.scan_at, TableStats.delete_file_pct).where(
                    TableStats.table_id.in_(ids)
                )
            ).all()
            stats = {row[0]: (row[1], row[2]) for row in stats_rows}
            run_times: dict[uuid.UUID, list[datetime]] = {}
            for table_id, created_at in session.execute(
                select(MaintenanceJob.table_id, MaintenanceRun.created_at)
                .join(MaintenanceRun, MaintenanceRun.id == MaintenanceJob.run_id)
                .where(
                    MaintenanceJob.table_id.in_(ids),
                    MaintenanceRun.trigger.like(f"{CONTINUOUS_TRIGGER_PREFIX}:%"),
                    MaintenanceRun.created_at >= day_ago,
                )
                .order_by(MaintenanceRun.created_at.asc())
                .limit(_RUNS_PER_DAY_SCAN_LIMIT)
            ).all():
                run_times.setdefault(table_id, []).append(db_instant(created_at))
            churn = dict(
                session.execute(
                    select(AuditLog.table_id, func.count())
                    .where(
                        AuditLog.table_id.in_(ids),
                        AuditLog.status == "OK",
                        AuditLog.action.in_(SNAPSHOT_COMMITTING_ACTIONS),
                        AuditLog.ts >= day_ago,
                    )
                    .group_by(AuditLog.table_id)
                )
                .tuples()
                .all()
            )

        result: dict[str, ContinuousTableState] = {}
        for table_id in ids:
            drift = drifts.get(table_id)
            scan_at, delete_pct = stats.get(table_id, (None, None))
            times = run_times.get(table_id, [])
            last_hour = [at for at in times if at >= hour_ago]
            score_drop = 0
            if drift is not None and drift.baseline_score is not None:
                score_drop = max(0, drift.baseline_score - (drift.estimated_score or 0))
            maintained = last_maintained.get(str(table_id))
            result[str(table_id)] = ContinuousTableState(
                table_id=str(table_id),
                drift=DriftView(
                    commits=int(drift.commits) if drift else 0,
                    small_files_added=int(drift.small_files_added) if drift else 0,
                    delete_file_ratio=float(delete_pct or 0.0) / 100.0,
                    score_drop=score_drop,
                    first_commit_at=_instant(drift.first_commit_at) if drift else None,
                    last_commit_at=_instant(drift.last_commit_at) if drift else None,
                ),
                history=History(
                    last_maintained_at=_instant(maintained),
                    last_continuous_at=times[-1] if times else None,
                    runs_last_hour=len(last_hour),
                    runs_last_day=len(times),
                    maintenance_commits_last_day=int(churn.get(table_id, 0) or 0),
                    oldest_run_last_hour=last_hour[0] if last_hour else None,
                    oldest_run_last_day=times[0] if times else None,
                ),
                scan_at=_instant(scan_at),
            )
        return result

    def audit(
        self,
        table_id: str,
        action: str,
        *,
        status: str,
        detail: str,
        engine: str | None = None,
    ) -> None:
        try:
            with self._session_factory() as session:
                session.add(
                    AuditLog(
                        table_id=uuid.UUID(table_id),
                        action=action,
                        status=status,
                        detail=detail,
                        engine=engine,
                        actor="system:scheduler",
                        origin="continuous",
                    )
                )
                session.commit()
        except Exception as exc:
            logger.warning("Continuous audit %s for table %s failed: %s", action, table_id, exc)

    def request_rescan(
        self,
        table_id: str,
        *,
        redis_client: Any,
        trigger: str = "continuous",
        requested_by: str = "system:scheduler",
    ) -> bool:
        """Queue a one-table scan so the planner sees the partitions the writer touched.
        False when the scan could not be requested; an already-active scan counts as done."""
        from cryo.db.repositories import datalake
        from cryo.queue.scan_queue import RedisScanQueue
        from cryo.scanner.control import enqueue_scan_run

        payload: dict[str, Any] = {
            "scope": "tables",
            "table_ids": [uuid.UUID(table_id)],
            "trigger": trigger,
            "requested_by": requested_by,
            "full_scan": True,
        }
        try:
            with self._session_factory() as session:
                try:
                    result = datalake.create_scan_trigger(session, payload)
                except ValueError as exc:
                    logger.info("Continuous rescan for %s not created: %s", table_id, exc)
                    return True
                if redis_client is not None:
                    enqueue_scan_run(
                        session,
                        RedisScanQueue(redis_client),
                        run_id=uuid.UUID(str(result["run_id"])),
                        num_chunks=1,
                        payload={
                            "scope": "tables",
                            "source_id": None,
                            "table_ids": [table_id],
                            "table_names": [],
                            "schema_names": [],
                            "cost_full_scan": False,
                            "full_scan": True,
                            "trigger": trigger,
                        },
                    )
            return True
        except Exception as exc:
            logger.warning("Continuous rescan for %s failed: %s", table_id, exc)
            return False


def _uuids(values: Sequence[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for value in values:
        try:
            out.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return out


def _instant(value: datetime | None) -> datetime | None:
    return db_instant(value) if value is not None else None
