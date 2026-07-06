from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from cryo.api.predicates import compile_predicate
from cryo.db.model_utils import qualified_table_name
from cryo.db.models import (
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    PresetRule,
    Source,
    Table,
    TableAssignment,
    TableStats,
)
from cryo.db.repositories.sources import table_in_source_scope
from cryo.scheduler.rules import (
    EffectivePreset,
    PresetConfig,
    TableCandidate,
    effective_preset,
    tables_for_preset,
)
from cryo.scheduler.rules import (
    PresetRule as RuleConfig,
)
from cryo.scheduler.rules import (
    TableAssignment as AssignmentConfig,
)


@dataclass(frozen=True)
class SelectionResult:
    table_ids: list[uuid.UUID]

    @property
    def affected(self) -> int:
        return len(self.table_ids)


def preset_to_config(preset: Preset) -> PresetConfig:
    return PresetConfig(
        id=str(preset.id),
        name=preset.name,
        destination_id=str(preset.destination_id),
        mode=preset.mode,
        params=preset.params,
        guardrails=preset.guardrails,
        priority=preset.priority,
        enabled=preset.enabled,
        schedule_cron=preset.schedule_cron,
    )


def rule_to_config(rule: PresetRule) -> RuleConfig:
    return RuleConfig(
        id=str(rule.id),
        preset_id=str(rule.preset_id),
        predicate=rule.predicate,
        enabled=rule.enabled,
    )


def assignment_to_config(assignment: TableAssignment) -> AssignmentConfig:
    return AssignmentConfig(
        table_id=str(assignment.table_id),
        preset_id=str(assignment.preset_id),
        pinned=assignment.pinned,
    )


def table_to_candidate(table: Table) -> TableCandidate:
    stats = _stats_dict(table.stats)
    if table.cost is not None:
        stats["cost_precision"] = table.cost.precision
    return TableCandidate(
        id=str(table.id),
        schema_name=table.schema_name,
        table_name=table.table_name,
        stats=stats,
        is_active=table.is_active,
    )


def get_preset_or_404(session: Session, preset_id: uuid.UUID) -> Preset | None:
    return session.get(Preset, preset_id)


def get_table_with_stats(session: Session, table_id: uuid.UUID) -> Table | None:
    return session.scalar(
        select(Table)
        .where(Table.id == table_id)
        .options(selectinload(Table.stats), selectinload(Table.assignment))
    )


def load_resolution_inputs(
    session: Session,
) -> tuple[list[PresetConfig], list[RuleConfig], list[AssignmentConfig]]:
    presets = [preset_to_config(item) for item in session.scalars(select(Preset))]
    rules = [rule_to_config(item) for item in session.scalars(select(PresetRule))]
    assignments = [assignment_to_config(item) for item in session.scalars(select(TableAssignment))]
    return presets, rules, assignments


def resolve_effective_preset(session: Session, table: Table) -> EffectivePreset | None:
    presets, rules, assignments = load_resolution_inputs(session)
    return effective_preset(table_to_candidate(table), presets, rules, assignments)


def simulate_preset(
    session: Session,
    preset: Preset,
    *,
    sample_limit: int = 20,
) -> tuple[list[TableCandidate], dict[str, float | int]]:
    presets, rules, assignments = load_resolution_inputs(session)
    candidates = [
        table_to_candidate(table)
        for table in session.scalars(
            select(Table).outerjoin(TableStats).options(selectinload(Table.stats))
        )
    ]
    matched = tables_for_preset(preset_to_config(preset), candidates, presets, rules, assignments)
    estimate = {
        "matched_count": len(matched),
        "total_size_gb": round(
            sum(float(item.stats.get("total_size_gb") or 0) for item in matched), 3
        ),
        "small_files_count": sum(int(item.stats.get("small_files_count") or 0) for item in matched),
    }
    return matched[:sample_limit], estimate


def resolve_selection(
    session: Session,
    *,
    mode: str,
    ids: Sequence[uuid.UUID],
    filter_payload: Mapping[str, Any] | None,
) -> SelectionResult:
    if mode == "ids":
        if not ids:
            raise ValueError("ids selection requires at least one id")
        existing = list(session.scalars(select(Table.id).where(Table.id.in_(ids))))
        missing = set(ids) - set(existing)
        if missing:
            missing_ids = sorted(str(item) for item in missing)
            raise ValueError(f"Unknown table ids: {missing_ids}")
        return SelectionResult(_filter_ids_in_maintenance_scope(session, existing))

    if mode != "filter":
        raise ValueError(f"Unsupported selection mode: {mode}")
    if not filter_payload:
        raise ValueError("filter selection requires a filter predicate")

    predicate = compile_predicate(filter_payload)
    stmt = select(Table.id).outerjoin(TableStats)
    if predicate is not None:
        stmt = stmt.where(predicate)
    return SelectionResult(_filter_ids_in_maintenance_scope(session, list(session.scalars(stmt))))


def _filter_ids_in_maintenance_scope(
    session: Session, table_ids: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    if not table_ids:
        return []
    rows = session.execute(
        select(Table, Source)
        .join(Source, Table.source_id == Source.id)
        .where(Table.id.in_(table_ids), Table.is_active.is_(True))
    )
    return [
        table.id
        for table, source in rows
        if table_in_source_scope(table, source)
    ]


def assign_tables(
    session: Session,
    *,
    table_ids: Sequence[uuid.UUID],
    preset_id: uuid.UUID,
    assigned_by: str | None = None,
) -> int:
    for table_id in table_ids:
        assignment = session.get(TableAssignment, table_id)
        if assignment is None:
            assignment = TableAssignment(table_id=table_id, preset_id=preset_id)
            session.add(assignment)
        assignment.preset_id = preset_id
        assignment.assigned_by = assigned_by
        assignment.pinned = True
    return len(table_ids)


def unassign_tables(session: Session, *, table_ids: Sequence[uuid.UUID]) -> int:
    if not table_ids:
        return 0
    existing = list(
        session.scalars(
            select(TableAssignment.table_id).where(TableAssignment.table_id.in_(table_ids))
        )
    )
    session.execute(delete(TableAssignment).where(TableAssignment.table_id.in_(table_ids)))
    return len(existing)


def run_summary(run: MaintenanceRun) -> dict[str, object]:
    summary = dict(run.summary or {})
    summary.update(_run_target_summary(run))
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "preset_id": run.preset_id,
        "trigger": run.trigger,
        "deadline_at": run.deadline_at,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "summary": summary,
    }


def _run_target_summary(run: MaintenanceRun) -> dict[str, object]:
    if run.kind == "scan":
        summary = dict(run.summary or {})
        scope = str(summary.get("scope") or "all")
        if scope == "tables":
            table_count = len(summary.get("table_ids") or [])
            target_label = f"Scan: {table_count} table{'s' if table_count != 1 else ''}"
        elif scope == "schemas":
            schema_names = summary.get("schema_names") or []
            target_label = (
                f"Scan: {', '.join(map(str, schema_names[:2]))}"
                if schema_names
                else "Scan: schemas"
            )
            if len(schema_names) > 2:
                target_label = f"{target_label} +{len(schema_names) - 2}"
        elif scope == "source":
            target_label = "Scan: source"
        else:
            target_label = "Scan: all"
        return {"target_label": target_label, "target_kind": "scan"}

    jobs = list(run.jobs or [])
    if not jobs:
        return {"target_label": "No jobs", "target_kind": run.kind}

    first = sorted(
        jobs,
        key=lambda job: (
            job.table.schema_name if job.table else "",
            job.table.table_name if job.table else "",
            str(job.id),
        ),
    )[0]
    if first.table is not None:
        table_ref = qualified_table_name(first.table)
        table_id = str(first.table.id)
    else:
        table_ref = str(first.table_id)
        table_id = str(first.table_id)

    suffix = f" +{len(jobs) - 1} tables" if len(jobs) > 1 else ""
    return {
        "target_label": f"{table_ref}{suffix}",
        "target_kind": "table" if len(jobs) == 1 else "tables",
        "target_table_id": table_id,
        "target_schema_name": first.table.schema_name if first.table else None,
        "target_table_name": first.table.table_name if first.table else None,
        "job_count": len(jobs),
    }


def job_summary(job: MaintenanceJob) -> dict[str, object]:
    table = job.table
    destination = job.destination
    return {
        "id": job.id,
        "run_id": job.run_id,
        "table_id": job.table_id,
        "destination_id": job.destination_id,
        "schema_name": table.schema_name if table else None,
        "table_name": table.table_name if table else None,
        "destination_name": destination.name if destination else None,
        "destination_engine": destination.engine if destination else None,
        "mode": job.mode,
        "status": job.status,
        "engine_ref": job.engine_ref,
        "replica": job.replica,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
        "plan": job.plan,
    }


def _stats_dict(stats: TableStats | None) -> dict[str, Any]:
    if stats is None:
        return {}
    return {
        "needs_compaction": stats.needs_compaction,
        "compaction_priority": stats.compaction_priority,
        "grade": stats.grade,
        "overall_score": stats.overall_score,
        "total_files": stats.total_files,
        "small_files_count": stats.small_files_count,
        "total_partitions": stats.total_partitions,
        "total_size_gb": stats.total_size_gb,
        "partition_col": stats.partition_col,
        "partition_col_type": stats.partition_col_type,
        "query_count_30d": stats.query_count_30d,
        "projected_savings_usd_month": stats.projected_savings_usd_month,
        "projected_monthly_s3_cost_usd": stats.projected_monthly_s3_cost_usd,
        "reclaimable_bytes": stats.reclaimable_bytes,
    }
