from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from cryo.queue.redis_queue import MaintenanceQueue
from cryo.scheduler.adaptive_routing import AdaptiveRoutingContext, resolve_destination_adaptive
from cryo.scheduler.cron import cron_is_due
from cryo.scheduler.routing import build_destinations_index, resolve_destination
from cryo.scheduler.rules import PresetConfig, PresetRule, TableAssignment, TableCandidate
from cryo.scheduler.scheduler import ScheduledRunDraft, build_scheduled_run, enqueue_run


class SchedulerStore(Protocol):
    def list_due_presets(self, now: datetime) -> list[tuple[PresetConfig, datetime | None]]: ...

    def list_tables(self) -> list[TableCandidate]: ...

    def list_presets(self) -> list[PresetConfig]: ...

    def list_rules(self) -> list[PresetRule]: ...

    def list_assignments(self) -> list[TableAssignment]: ...

    def list_destinations(self) -> list[dict[str, Any]]: ...

    def persist_run(self, run: ScheduledRunDraft) -> None: ...

    def mark_preset_run(self, preset_id: str, run_at: datetime) -> None: ...


@dataclass(frozen=True)
class SchedulerTickResult:
    runs_created: int = 0
    jobs_enqueued: int = 0
    preset_ids: tuple[str, ...] = ()


class SchedulerService:
    def __init__(
        self,
        store: SchedulerStore,
        queue: MaintenanceQueue,
        *,
        leader_check: Callable[[], bool] | None = None,
    ) -> None:
        self._store = store
        self._queue = queue
        self._leader_check = leader_check or (lambda: True)

    def tick(self, *, now: datetime | None = None) -> SchedulerTickResult:
        if not self._leader_check():
            return SchedulerTickResult()

        current = now or datetime.now(UTC)
        due_presets = self._store.list_due_presets(current)
        if not due_presets:
            return SchedulerTickResult()

        tables = self._store.list_tables()
        presets = self._store.list_presets()
        rules = self._store.list_rules()
        assignments = self._store.list_assignments()
        destinations = build_destinations_index(self._store.list_destinations())

        runs_created = 0
        jobs_enqueued = 0
        fired: list[str] = []

        for preset, last_run_at in due_presets:
            if not cron_is_due(preset.schedule_cron, now=current, last_run_at=last_run_at):
                continue

            run = build_scheduled_run(
                preset,
                tables,
                presets,
                rules,
                assignments,
                trigger="cron",
                now=current,
            )
            if not run.jobs:
                self._store.mark_preset_run(preset.id, current)
                continue

            routed_jobs = _apply_routing(run, preset, tables, destinations)
            self._store.persist_run(routed_jobs)
            enqueue_run(routed_jobs, self._queue)
            self._store.mark_preset_run(preset.id, current)

            runs_created += 1
            jobs_enqueued += len(routed_jobs.jobs)
            fired.append(preset.id)

        return SchedulerTickResult(
            runs_created=runs_created,
            jobs_enqueued=jobs_enqueued,
            preset_ids=tuple(fired),
        )


def _apply_routing(
    run: ScheduledRunDraft,
    preset: PresetConfig,
    tables: list[TableCandidate],
    destinations: dict[str, dict[str, Any]],
) -> ScheduledRunDraft:
    tables_by_id = {table.id: table for table in tables}
    routed = []
    for job in run.jobs:
        table = tables_by_id.get(job.table_id)
        destination_id = job.destination_id
        if table is not None:
            if preset.guardrails.get("adaptive_routing"):
                route = resolve_destination_adaptive(
                    table,
                    preset,
                    destinations,
                    AdaptiveRoutingContext(),
                )
            else:
                route = resolve_destination(table, preset, destinations)
            destination_id = route.destination_id
        routed.append(
            type(job)(
                id=job.id,
                run_id=job.run_id,
                table_id=job.table_id,
                destination_id=destination_id,
                mode=job.mode,
                status=job.status,
            )
        )
    return ScheduledRunDraft(
        id=run.id,
        preset_id=run.preset_id,
        trigger=run.trigger,
        deadline_at=run.deadline_at,
        jobs=tuple(routed),
    )


class InMemorySchedulerStore:
    """In-memory scheduler store for tests and local CLI."""

    def __init__(self) -> None:
        self.presets: list[PresetConfig] = []
        self.rules: list[PresetRule] = []
        self.assignments: list[TableAssignment] = []
        self.tables: list[TableCandidate] = []
        self.destinations: list[dict[str, Any]] = []
        self.last_runs: dict[str, datetime] = {}
        self.runs: list[ScheduledRunDraft] = []

    def list_due_presets(self, now: datetime) -> list[tuple[PresetConfig, datetime | None]]:
        del now
        return [
            (preset, self.last_runs.get(preset.id))
            for preset in self.presets
            if preset.enabled and preset.schedule_cron
        ]

    def list_tables(self) -> list[TableCandidate]:
        return list(self.tables)

    def list_presets(self) -> list[PresetConfig]:
        return list(self.presets)

    def list_rules(self) -> list[PresetRule]:
        return list(self.rules)

    def list_assignments(self) -> list[TableAssignment]:
        return list(self.assignments)

    def list_destinations(self) -> list[dict[str, Any]]:
        return list(self.destinations)

    def persist_run(self, run: ScheduledRunDraft) -> None:
        self.runs.append(run)

    def mark_preset_run(self, preset_id: str, run_at: datetime) -> None:
        self.last_runs[preset_id] = run_at


def new_job_id() -> str:
    return str(uuid4())
