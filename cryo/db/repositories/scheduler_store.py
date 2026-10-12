from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
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


class SQLSchedulerStore:
    def __init__(self, session_factory: Callable[[], Session], redis_client: Any) -> None:
        self._session_factory = session_factory
        self._redis = redis_client
        self._namespace = "cryo"

    def list_due_presets(self, now: datetime) -> list[tuple[PresetConfig, datetime | None]]:
        del now
        with self._session_factory() as session:
            presets = session.scalars(select(Preset).where(Preset.enabled.is_(True)))
            due: list[tuple[PresetConfig, datetime | None]] = []
            for preset in presets:
                if not preset.schedule_cron:
                    continue
                config = preset_to_config(preset)
                last_run = self._get_last_run(config.id)
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
                .where(Table.is_active.is_(True))
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

            last_maintained_by_table = dict(
                session.execute(
                    select(AuditLog.table_id, func.max(AuditLog.ts))
                    .where(AuditLog.status == "OK", AuditLog.action.in_(_RECLAIM_ACTIONS))
                    .group_by(AuditLog.table_id)
                ).all()
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

    def persist_run(self, run: ScheduledRunDraft) -> None:
        with self._session_factory() as session:
            db_run = MaintenanceRun(
                id=uuid.UUID(run.id),
                kind="scheduled",
                preset_id=uuid.UUID(run.preset_id),
                trigger=run.trigger,
                status="queued",
                deadline_at=run.deadline_at,
                summary=correlation_summary({"actor": "system:scheduler"}),
            )
            session.add(db_run)
            db_jobs: list[MaintenanceJob] = []
            for job in run.jobs:
                db_job = MaintenanceJob(
                    id=uuid.UUID(job.id),
                    run_id=uuid.UUID(run.id),
                    table_id=uuid.UUID(job.table_id),
                    destination_id=uuid.UUID(job.destination_id),
                    mode=job.mode.value if hasattr(job.mode, "value") else str(job.mode),
                    status=job.status.value,
                )
                session.add(db_job)
                db_jobs.append(db_job)
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

    def mark_preset_run(self, preset_id: str, run_at: datetime) -> None:
        key = f"{self._namespace}:scheduler:last_run:{preset_id}"
        self._redis.set(key, run_at.isoformat())

    def _get_last_run(self, preset_id: str) -> datetime | None:
        key = f"{self._namespace}:scheduler:last_run:{preset_id}"
        raw = self._redis.get(key)
        if raw is None:
            return None
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
