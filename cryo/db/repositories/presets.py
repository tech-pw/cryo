from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, cast, delete, func, select
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
    TableCost,
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


def clear_other_default_presets(
    session: Session,
    *,
    tenant_id: uuid.UUID | None,
    keep_preset_id: uuid.UUID | None = None,
) -> None:
    stmt = select(Preset).where(Preset.is_default.is_(True))
    if tenant_id is None:
        stmt = stmt.where(Preset.tenant_id.is_(None))
    else:
        stmt = stmt.where(Preset.tenant_id == tenant_id)
    if keep_preset_id is not None:
        stmt = stmt.where(Preset.id != keep_preset_id)
    for preset in session.scalars(stmt):
        preset.is_default = False


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
        is_default=bool(preset.is_default),
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


def table_to_candidate(table: Table, *, last_maintained_at: Any | None = None) -> TableCandidate:
    """last_maintained_at is caller-supplied, never queried here -- table_to_candidate is a
    pure Table->TableCandidate transform every existing call site treats as DB-call-free
    (several loop over it per-table; see e.g. SQLSchedulerStore.list_tables' own
    STATS_LOAD_ONLY_COLUMNS comment for the OOM this class of per-table-query bug already
    caused once). Omitting it means TableCandidate.last_maintained_at stays None, same as
    before this parameter existed -- so scheduler.drop_recently_maintained's cooldown check
    silently never drops anything for callers that don't pass a real value. Batch-fetch it
    (one grouped query for the whole fleet, scoped to real reclaim actions -- see
    cryo.reporting.impact_signals._RECLAIM_ACTIONS) and pass it in per-table, matching the
    pattern dashboard_routes.py's SLA endpoint already uses for the same field."""
    stats = _stats_dict(table.stats)
    if table.cost is not None:
        stats["cost_precision"] = table.cost.precision
    return TableCandidate(
        id=str(table.id),
        schema_name=table.schema_name,
        table_name=table.table_name,
        stats=stats,
        is_active=table.is_active,
        last_maintained_at=last_maintained_at,
        # Critical vitals action plan, 2026-09-20: bounded containment for a table whose
        # metadata was confirmed missing in S3 (cryo.db.repositories.sources.
        # record_missing_metadata) -- see cryo.scheduler.rules.tables_for_preset's gate on
        # this field for the actual exclusion logic.
        maintenance_retry_after=table.maintenance_retry_after,
    )


def get_preset_or_404(session: Session, preset_id: uuid.UUID) -> Preset | None:
    return session.get(Preset, preset_id)


def get_table_with_stats(session: Session, table_id: uuid.UUID) -> Table | None:
    return session.scalar(
        select(Table)
        .where(Table.id == table_id)
        .options(selectinload(Table.stats), selectinload(Table.assignment))
    )


def list_compatible_presets(session: Session, table: Table) -> list[Preset]:
    """Return presets whose destination can serve this table's catalog.

    Non-Trino destinations are always compatible. Trino destinations require an
    explicit ``catalog_bindings`` entry for ``table.source_id``.
    """
    from cryo.engines.trino.catalog_bindings import destination_serves_source

    presets = list(
        session.scalars(
            select(Preset).options(selectinload(Preset.destination)).order_by(Preset.name)
        )
    )
    return [
        preset
        for preset in presets
        if destination_serves_source(
            preset.destination.engine,
            preset.destination.config,
            table.source_id,
        )
    ]


def assert_preset_compatible_with_tables(
    session: Session,
    *,
    preset_id: uuid.UUID,
    table_ids: Sequence[uuid.UUID],
) -> None:
    """Raise ValueError if the preset's Trino destination does not bind any table's source."""
    from cryo.engines.trino.catalog_bindings import destination_serves_source

    if not table_ids:
        return
    preset = session.scalar(
        select(Preset).where(Preset.id == preset_id).options(selectinload(Preset.destination))
    )
    if preset is None:
        raise ValueError("Preset does not exist")
    tables = list(session.scalars(select(Table).where(Table.id.in_(list(table_ids)))))
    found = {table.id for table in tables}
    missing = [str(tid) for tid in table_ids if tid not in found]
    if missing:
        raise ValueError(f"Tables not found: {', '.join(missing)}")
    incompatible = [
        f"{table.schema_name}.{table.table_name}"
        for table in tables
        if not destination_serves_source(
            preset.destination.engine,
            preset.destination.config,
            table.source_id,
        )
    ]
    if incompatible:
        raise ValueError(
            "Preset's Trino destination does not include the catalog for: "
            + ", ".join(incompatible)
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
            select(Table)
            .where(Table.is_active.is_(True))
            .options(
                selectinload(Table.stats).load_only(*STATS_LOAD_ONLY_COLUMNS),
                selectinload(Table.cost).load_only(*COST_LOAD_ONLY_COLUMNS),
            )
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
    stmt = select(Table.id).outerjoin(TableStats).outerjoin(TableCost).outerjoin(TableAssignment)
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
    return [table.id for table, source in rows if table_in_source_scope(table, source)]


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


# Per-table name lists, only ever read on the single-run detail page -- a fleet-wide scan's
# summary carries one entry per unreadable/failed table (12.8KB+ each on a real run, confirmed
# live 2026-10-06: GET /runs?limit=200 took 17s/529KB and the Runs page's "Running now"/"Failed
# runs" counts silently showed (0), since the request never resolved before the UI gave up and
# fell back to an empty list). Dropped from list responses; see run_summary's detail=False.
_DETAIL_ONLY_SUMMARY_KEYS = ("unreadable_tables", "failed_tables")


def run_summary(
    run: MaintenanceRun, *, target: dict[str, object] | None = None, detail: bool = False
) -> dict[str, object]:
    """``target`` comes from :func:`run_target_summaries` on list paths; without it the
    run's jobs are loaded, which is only acceptable for a single run.

    ``detail=False`` (the list-endpoint default) strips the large per-table name arrays a
    fleet-wide scan's summary can carry -- list views never read them, only the single-run
    detail page does (``detail=True``).
    """
    summary = dict(run.summary or {})
    summary.update(target if target is not None else _run_target_summary(run))
    if not detail:
        for key in _DETAIL_ONLY_SUMMARY_KEYS:
            summary.pop(key, None)
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "preset_id": run.preset_id,
        "trigger": run.trigger,
        "deadline_at": run.deadline_at,
        "scheduled_at": run.scheduled_at,
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
    return _job_target_summary(len(jobs), first.table_id, first.table)


def _job_target_summary(
    job_count: int, first_table_id: uuid.UUID, first_table: Table | None
) -> dict[str, object]:
    if first_table is not None:
        table_ref = qualified_table_name(first_table)
        table_id = str(first_table.id)
    else:
        table_ref = str(first_table_id)
        table_id = str(first_table_id)

    suffix = f" +{job_count - 1} tables" if job_count > 1 else ""
    return {
        "target_label": f"{table_ref}{suffix}",
        "target_kind": "table" if job_count == 1 else "tables",
        "target_table_id": table_id,
        "target_schema_name": first_table.schema_name if first_table else None,
        "target_table_name": first_table.table_name if first_table else None,
        "job_count": job_count,
    }


def run_target_summaries(
    session: Session, runs: Sequence[MaintenanceRun]
) -> dict[uuid.UUID, dict[str, object]]:
    """Target labels for many runs from one aggregate query instead of loading every job
    (a scheduled run can own thousands)."""
    targets: dict[uuid.UUID, dict[str, object]] = {}
    job_run_ids = []
    for run in runs:
        if run.kind == "scan":
            targets[run.id] = _run_target_summary(run)
        else:
            targets[run.id] = {"target_label": "No jobs", "target_kind": run.kind}
            job_run_ids.append(run.id)
    if not job_run_ids:
        return targets

    ranked = (
        select(
            MaintenanceJob.run_id,
            MaintenanceJob.table_id,
            func.count().over(partition_by=MaintenanceJob.run_id).label("job_count"),
            func.row_number()
            .over(
                partition_by=MaintenanceJob.run_id,
                order_by=(
                    func.coalesce(Table.schema_name, ""),
                    func.coalesce(Table.table_name, ""),
                    cast(MaintenanceJob.id, String),
                ),
            )
            .label("rank"),
        )
        .outerjoin(Table, Table.id == MaintenanceJob.table_id)
        .where(MaintenanceJob.run_id.in_(job_run_ids))
        .subquery()
    )
    firsts = session.execute(
        select(ranked.c.run_id, ranked.c.table_id, ranked.c.job_count).where(ranked.c.rank == 1)
    ).all()
    tables = {
        table.id: table
        for table in session.scalars(
            select(Table)
            .where(Table.id.in_({row.table_id for row in firsts}))
            .options(selectinload(Table.source))
        )
    }
    for row in firsts:
        targets[row.run_id] = _job_target_summary(
            int(row.job_count), row.table_id, tables.get(row.table_id)
        )
    return targets


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
        "force_admit": job.force_admit,
    }


# Columns _stats_dict actually reads. Confirmed live, 2026-08-10: eager-loading
# TableStats via a bare `selectinload(Table.stats)` (no column restriction) pulls every
# column, including `raw` -- a JSONB blob per table holding per-column quality stats,
# a sample of file paths, health deductions, etc., easily tens of KB per row. Across the
# full fleet (~13,600 tables) that materializes into a single Python process all at once
# for both `simulate_preset` and the scheduler's own `list_tables()` (used by every
# cron tick) -- OOM-killed the API pod (6Gi limit) on a fleet-wide predicate. `load_only`
# restricts what SQLAlchemy actually selects from the DB for this relationship to just
# the columns below, cutting the per-table payload by roughly two orders of magnitude.
# Keep this in sync with `_stats_dict`'s own field list below.
STATS_LOAD_ONLY_COLUMNS = (
    TableStats.needs_compaction,
    TableStats.compaction_priority,
    TableStats.grade,
    TableStats.overall_score,
    TableStats.total_files,
    TableStats.small_files_count,
    TableStats.total_partitions,
    TableStats.total_size_gb,
    TableStats.partition_col,
    TableStats.partition_col_type,
    TableStats.query_count_30d,
    TableStats.projected_savings_usd_month,
    TableStats.projected_monthly_s3_cost_usd,
    TableStats.reclaimable_bytes,
    # Not read by _stats_dict but part of predicates.py's FIELD_MAP, so predicate
    # evaluation needs it loaded even though it isn't copied into the candidate dict.
    TableStats.estimated_monthly_s3_cost_usd,
    TableStats.last_snapshot_committed_at,
    # Added 2026-08-10 alongside FIELD_MAP support for backlog-based rules (e.g.
    # total_snapshots > 100) -- same "predicate needs it loaded, _stats_dict doesn't
    # necessarily read it" rationale as estimated_monthly_s3_cost_usd above.
    TableStats.total_snapshots,
    TableStats.manifest_count,
)
# TableCost's only field _stats_dict reads is `precision` -- same rationale as above,
# TableCost.raw is the same class of large per-table JSONB blob.
COST_LOAD_ONLY_COLUMNS = (TableCost.precision,)


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
        # Added 2026-08-10 alongside FIELD_MAP support for total_snapshots/manifest_count.
        # PresetRule matching (scheduler/rules.py::tables_for_preset) evaluates predicates
        # against this dict via evaluate_predicate(), not the SQL-side FIELD_MAP -- a field
        # present only in FIELD_MAP (like estimated_monthly_s3_cost_usd, added earlier) works
        # for the /tables API's `filter` query param but silently never matches as a
        # PresetRule predicate, since row.get() on a missing key returns None. Both fields
        # need to be here for a rule like `total_snapshots > 100` to actually route tables.
        "total_snapshots": stats.total_snapshots,
        "manifest_count": stats.manifest_count,
    }
