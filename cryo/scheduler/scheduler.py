from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.domain.enums import JobStatus, Mode
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.scheduler.rules import (
    PresetConfig,
    PresetRule,
    TableAssignment,
    TableCandidate,
    tables_for_preset,
)


@dataclass(frozen=True)
class ScheduledJobDraft:
    id: str
    run_id: str
    table_id: str
    destination_id: str
    mode: Mode
    status: JobStatus = JobStatus.QUEUED


@dataclass(frozen=True)
class ScheduledRunDraft:
    id: str
    preset_id: str
    trigger: str
    deadline_at: datetime | None
    jobs: tuple[ScheduledJobDraft, ...] = field(default_factory=tuple)


def build_scheduled_run(
    preset: PresetConfig,
    tables: Iterable[TableCandidate],
    presets: Iterable[PresetConfig],
    rules: Iterable[PresetRule],
    assignments: Iterable[TableAssignment] = (),
    *,
    trigger: str = "cron",
    budget_hint: int | None = None,
    now: datetime | None = None,
) -> ScheduledRunDraft:
    current = now or datetime.now(UTC)
    candidates = tables_for_preset(preset, tables, presets, rules, assignments)
    candidates = drop_recently_maintained(candidates, preset)
    candidates = apply_priority_order(candidates)
    if budget_hint is not None:
        candidates = candidates[:budget_hint]

    run_id = str(uuid4())
    deadline = _deadline_at(preset, current)
    jobs = tuple(
        ScheduledJobDraft(
            id=str(uuid4()),
            run_id=run_id,
            table_id=table.id,
            destination_id=preset.destination_id,
            mode=Mode(preset.mode),
        )
        for table in candidates
    )
    return ScheduledRunDraft(
        id=run_id,
        preset_id=preset.id,
        trigger=trigger,
        deadline_at=deadline,
        jobs=jobs,
    )


def enqueue_run(run: ScheduledRunDraft, queue: MaintenanceQueue, *, priority: int = 0) -> None:
    for job in run.jobs:
        queue.enqueue(job.id, job.destination_id, priority)


def drop_recently_maintained(
    tables: Iterable[TableCandidate],
    preset: PresetConfig,
    *,
    now: datetime | None = None,
) -> list[TableCandidate]:
    cooldown_hours = int(preset.guardrails.get("cooldown_hours", 0) or 0)
    if cooldown_hours <= 0:
        return list(tables)

    current = now or datetime.now(UTC)
    kept: list[TableCandidate] = []
    for table in tables:
        last = table.last_maintained_at
        if not isinstance(last, datetime):
            kept.append(table)
            continue
        if _ensure_aware(last) + timedelta(hours=cooldown_hours) <= _ensure_aware(current):
            kept.append(table)
    return kept


def apply_priority_order(tables: Iterable[TableCandidate]) -> list[TableCandidate]:
    priority_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(
        tables,
        key=lambda table: (
            priority_rank.get(str(table.stats.get("compaction_priority", "LOW")), 3),
            -int(table.stats.get("small_files_count") or 0),
            table.schema_name,
            table.table_name,
        ),
    )


def _deadline_at(preset: PresetConfig, now: datetime) -> datetime | None:
    value = preset.guardrails.get("deadline_minutes")
    if value is None:
        return None
    return _ensure_aware(now) + timedelta(minutes=int(value))


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def main() -> None:
    raise SystemExit("Scheduler core is library-only in this phase.")


if __name__ == "__main__":
    main()
