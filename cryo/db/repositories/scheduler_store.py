from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from cryo.config import get_settings
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    PresetRule,
    Table,
    TableAssignment,
)
from cryo.db.repositories.exclusions import table_is_excluded
from cryo.db.repositories.intents import IntentStore
from cryo.db.repositories.job_dedupe import active_coverage
from cryo.db.repositories.presets import (
    COST_LOAD_ONLY_COLUMNS,
    STATS_LOAD_ONLY_COLUMNS,
    assignment_to_config,
    preset_to_config,
    rule_to_config,
    table_to_candidate,
)
from cryo.db.repositories.sources import table_in_source_scope
from cryo.intents.binding import build_and_bind_job_intent
from cryo.observability.correlation import correlation_summary
from cryo.planner.job_coverage import (
    IN_FLIGHT_JOB_STATUSES,
    ActiveJob,
    dedupe_key,
    effective_operations,
)
from cryo.scheduler.cron import db_instant
from cryo.scheduler.rules import (
    PresetConfig,
    TableCandidate,
)
from cryo.scheduler.rules import (
    PresetRule as PresetRuleConfig,
)
from cryo.scheduler.rules import (
    TableAssignment as TableAssignmentConfig,
)
from cryo.scheduler.scheduler import ScheduledRunDraft

logger = logging.getLogger(__name__)


class SQLSchedulerStore:
    def __init__(self, session_factory: Callable[[], Session], redis_client: Any) -> None:
        self._session_factory = session_factory
        self._redis = redis_client
        self._namespace = "cryo"

    def list_due_presets(self, now: datetime) -> list[tuple[PresetConfig, datetime | None]]:
        del now
        if IntentStore(self._session_factory).is_paused():
            return []
        with self._session_factory() as session:
            presets = [
                preset
                for preset in session.scalars(select(Preset).where(Preset.enabled.is_(True)))
                if preset.schedule_cron
            ]
            last_cron_runs = _last_cron_run_by_preset(session, [p.id for p in presets])
            due: list[tuple[PresetConfig, datetime | None]] = []
            for preset in presets:
                config = preset_to_config(preset)
                last_run = _latest(self._get_last_run(config.id), last_cron_runs.get(preset.id))
                due.append((config, last_run))
            return due

    def list_tables(self) -> list[TableCandidate]:
        with self._session_factory() as session:
            # STATS_LOAD_ONLY_COLUMNS / COST_LOAD_ONLY_COLUMNS restrict TableStats/TableCost
            # eager-loading to the handful of scalar columns rule/predicate evaluation
            # actually reads -- a bare `selectinload(Table.stats)` (no column restriction)
            # pulls every column including `raw`, a large per-table JSONB blob, for the
            # entire active fleet in one process. Confirmed live, 2026-08-10: this exact
            # unrestricted query OOM-killed the API pod (6Gi limit) via /simulate on a
            # fleet-wide (~13,600-table) predicate -- this is the scheduler's own tick path,
            # so an unrestricted load here would OOM the scheduler on every cron firing for
            # any preset whose rule matches a large share of the fleet, not just at
            # diagnostic time.
            tables = session.scalars(
                select(Table)
                .options(
                    selectinload(Table.stats).load_only(*STATS_LOAD_ONLY_COLUMNS),
                    selectinload(Table.cost).load_only(*COST_LOAD_ONLY_COLUMNS),
                    selectinload(Table.source),
                )
                .where(Table.is_active.is_(True), ~table_is_excluded())
            )
            # Real fix, 2026-09-10: TableCandidate.last_maintained_at used to stay None for
            # every candidate here -- table_to_candidate never populated it, so
            # scheduler.drop_recently_maintained's `isinstance(last, datetime)` check was
            # always False, meaning the cooldown filter at schedule-creation time silently
            # never dropped anything. One grouped query for the whole fleet (same batching
            # discipline as this function's own STATS_LOAD_ONLY_COLUMNS fix above), scoped
            # to real reclaim actions only -- see job_store._last_maintained_at's own
            # docstring for why expire-only/property-autopilot audits must not count.
            from cryo.reporting.impact_signals import _RECLAIM_ACTIONS

            last_maintained_by_table: dict[uuid.UUID | None, datetime] = dict(
                session.execute(
                    select(AuditLog.table_id, func.max(AuditLog.ts))
                    .where(AuditLog.status == "OK", AuditLog.action.in_(_RECLAIM_ACTIONS))
                    .group_by(AuditLog.table_id)
                )
                .tuples()
                .all()
            )
            return [
                table_to_candidate(table, last_maintained_at=last_maintained_by_table.get(table.id))
                for table in tables
                if table.source is not None and table_in_source_scope(table, table.source)
            ]

    def list_presets(self) -> list[PresetConfig]:
        with self._session_factory() as session:
            return [preset_to_config(p) for p in session.scalars(select(Preset))]

    def list_rules(self) -> list[PresetRuleConfig]:
        with self._session_factory() as session:
            return [rule_to_config(r) for r in session.scalars(select(PresetRule))]

    def list_assignments(self) -> list[TableAssignmentConfig]:
        with self._session_factory() as session:
            return [assignment_to_config(a) for a in session.scalars(select(TableAssignment))]

    def list_destinations(self) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return [
                {
                    "id": str(d.id),
                    "name": d.name,
                    "engine": d.engine,
                    "config": dict(d.config),
                }
                for d in session.scalars(select(Destination).where(Destination.enabled.is_(True)))
            ]

    def consecutive_engine_failures(
        self, table_ids: Sequence[str], *, lookback: int = 10
    ) -> dict[str, dict[str, int]]:
        """For each table, per destination it's actually been run against, count how many of
        its most recent (up to `lookback`) jobs failed consecutively -- most-recent-first,
        stopping at the first non-failed status -- so adaptive routing can stop repeatedly
        retrying a destination that keeps failing a specific table.

        Regression context, 2026-08-10: unified_bq.gold_dbt_pw_bq failed on the native engine
        4 times in a row across 8 days, 4 different error messages each time, and nothing in
        routing had any memory of that history -- adaptive_routing's own scoring (size/
        capacity/latency/cost) treats a table that's failed repeatedly identically to one
        that's never been tried. This is that missing memory.

        Deliberately scoped to only the table_ids the caller actually needs (a single
        scheduled run's job list, not the whole fleet) -- list_tables() already OOM-killed
        the API pod once this session from an unbounded fleet-wide query; this must not
        repeat that mistake by defaulting to "all tables" anywhere.
        """
        if not table_ids:
            return {}
        ids = [uuid.UUID(str(t)) for t in table_ids]
        with self._session_factory() as session:
            ranked = (
                select(
                    MaintenanceJob.table_id,
                    MaintenanceJob.destination_id,
                    MaintenanceJob.status,
                    func.row_number()
                    .over(
                        partition_by=(MaintenanceJob.table_id, MaintenanceJob.destination_id),
                        order_by=MaintenanceJob.started_at.desc(),
                    )
                    .label("rn"),
                )
                .where(
                    MaintenanceJob.table_id.in_(ids),
                    MaintenanceJob.started_at.is_not(None),
                    MaintenanceJob.status.in_(("succeeded", "failed", "partial")),
                )
                .subquery()
            )
            rows = session.execute(
                select(ranked.c.table_id, ranked.c.destination_id, ranked.c.status)
                .where(ranked.c.rn <= lookback)
                .order_by(ranked.c.table_id, ranked.c.destination_id, ranked.c.rn)
            ).all()

        result: dict[str, dict[str, int]] = {}
        current_key: tuple[str, str] | None = None
        streak = 0
        broken = False
        for table_id, destination_id, status in rows:
            key = (str(table_id), str(destination_id))
            if key != current_key:
                current_key = key
                streak = 0
                broken = False
            if not broken:
                if status == "failed":
                    streak += 1
                else:
                    broken = True
            if streak:
                result.setdefault(str(table_id), {})[str(destination_id)] = streak
        return result

    def active_coverage(self) -> dict[str, list[ActiveJob]]:
        with self._session_factory() as session:
            return active_coverage(session)

    def preset_backlog_state(self, preset_id: str, since: datetime) -> tuple[int, set[str]]:
        """(queued/admitted/running jobs of the preset, tables it has had a job for since
        `since`). The second set is what keeps a backlog batch from retrying a table the same
        cron period already attempted, whatever that attempt's outcome."""
        preset_uuid = uuid.UUID(preset_id)
        with self._session_factory() as session:
            in_flight = session.scalar(
                select(func.count())
                .select_from(MaintenanceJob)
                .join(MaintenanceRun, MaintenanceRun.id == MaintenanceJob.run_id)
                .where(
                    MaintenanceRun.preset_id == preset_uuid,
                    MaintenanceJob.status.in_(IN_FLIGHT_JOB_STATUSES),
                )
            )
            attempted = session.scalars(
                select(MaintenanceJob.table_id)
                .join(MaintenanceRun, MaintenanceRun.id == MaintenanceJob.run_id)
                .where(
                    MaintenanceRun.preset_id == preset_uuid,
                    MaintenanceRun.created_at >= since,
                )
                .distinct()
            ).all()
        return int(in_flight or 0), {str(table_id) for table_id in attempted}

    def persist_run(self, run: ScheduledRunDraft) -> set[str]:
        """Store the run and its jobs; returns ids of jobs dropped because a concurrent run
        took the same (table, operations) slot between coverage read and insert."""
        dropped: set[str] = set()
        with self._session_factory() as session:
            summary = correlation_summary({"actor": "system:scheduler"})
            if run.dedupe_tables_skipped or run.dedupe_steps_skipped:
                summary["dedupe"] = {
                    "tables_skipped": run.dedupe_tables_skipped,
                    "steps_skipped": run.dedupe_steps_skipped,
                }
            db_run = MaintenanceRun(
                id=uuid.UUID(run.id),
                kind="scheduled",
                preset_id=uuid.UUID(run.preset_id),
                trigger=run.trigger,
                status="queued",
                deadline_at=run.deadline_at,
                summary=summary,
            )
            session.add(db_run)
            session.flush()
            db_jobs: list[MaintenanceJob] = []
            for job in run.jobs:
                mode = job.mode.value if hasattr(job.mode, "value") else str(job.mode)
                operations = list(job.operations) if job.operations else None
                db_job = MaintenanceJob(
                    id=uuid.UUID(job.id),
                    run_id=uuid.UUID(run.id),
                    table_id=uuid.UUID(job.table_id),
                    destination_id=uuid.UUID(job.destination_id),
                    mode=mode,
                    operations=operations,
                    dedupe_key=dedupe_key(effective_operations(mode, job_operations=operations)),
                    status=job.status.value,
                )
                try:
                    with session.begin_nested():
                        session.add(db_job)
                except IntegrityError:
                    dropped.add(job.id)
                    continue
                db_jobs.append(db_job)
            if run.jobs and not db_jobs:
                session.rollback()
                return dropped
            session.flush()
            settings = get_settings()
            if settings.durable_intents_enabled:
                for db_job in db_jobs:
                    build_and_bind_job_intent(
                        session,
                        job_id=str(db_job.id),
                        settings=settings,
                        actor_type="scheduler",
                        actor_id="system:scheduler",
                    )
            session.commit()
        return dropped

    def mark_preset_run(self, preset_id: str, run_at: datetime) -> None:
        key = f"{self._namespace}:scheduler:last_run:{preset_id}"
        self._redis.set(key, run_at.isoformat())

    def _get_last_run(self, preset_id: str) -> datetime | None:
        key = f"{self._namespace}:scheduler:last_run:{preset_id}"
        try:
            raw = self._redis.get(key)
        except Exception:
            logger.warning("Redis last-run marker read failed for preset %s", preset_id)
            return None
        if raw is None:
            return None
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _last_cron_run_by_preset(
    session: Session, preset_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """Newest cron-triggered run per preset, from Postgres.

    The Redis marker is a cache: the bundled Redis has no persistence, so after a restart the
    marker is gone and cron_is_due's 24h catch-up re-fired every daily preset at whatever time
    Redis came back (#177). The runs table already records every fire that produced work.
    """
    if not preset_ids:
        return {}
    rows = session.execute(
        select(
            MaintenanceRun.preset_id,
            func.max(func.coalesce(MaintenanceRun.scheduled_at, MaintenanceRun.created_at)),
        )
        .where(
            MaintenanceRun.preset_id.in_(preset_ids),
            MaintenanceRun.trigger == "cron",
            MaintenanceRun.kind != "scan",
        )
        .group_by(MaintenanceRun.preset_id)
    ).all()
    return {
        preset_id: db_instant(last) for preset_id, last in rows if preset_id is not None and last
    }


def _latest(*values: datetime | None) -> datetime | None:
    present = [db_instant(value) for value in values if value is not None]
    return max(present) if present else None
