from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from cryo.config import get_settings
from cryo.db.models import (
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
            return [
                table_to_candidate(table)
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
