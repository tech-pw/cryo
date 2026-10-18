"""Per-table maintenance status for the Tables page, plus the fleet funnel behind its Sankey.

Every lookup here is keyed on an index: jobs (table_id, finished_at), skip_evaluations
(table_id, evaluated_at) and table_stats_history (table_id, scan_at). Page summaries are
bounded by the page's table ids; the funnel is one grouped aggregate, never a row list.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.orm import Session

from cryo.api.schemas import (
    FunnelLink,
    FunnelNode,
    TableJobRef,
    TableMaintenanceRead,
    TableMaintenanceState,
    TableSelectionRef,
    TablesFunnelResponse,
)
from cryo.db.models import (
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    Table,
    TableAssignment,
    TableStats,
    TableStatsHistory,
)
from cryo.db.repositories.exclusions import excluded_table_ids
from cryo.db.tenant_scope import scope_by_tenant
from cryo.domain.enums import JobStatus
from cryo.maintenance.skip_history import (
    REASON_BACKOFF,
    REASON_BUDGET_CAP,
    REASON_COOLDOWN,
    REASON_EXCLUDED,
    REASON_STALE_STATS,
    STAGE_ELIGIBLE,
    STAGE_EVALUATED,
    STAGE_NEEDS_WORK,
    SkipEvaluation,
)

SCORE_DELTA_DAYS = 7

ACTIVE_JOB_STATUSES = frozenset(
    {JobStatus.QUEUED, JobStatus.ADMITTED, JobStatus.RUNNING, JobStatus.DEFERRED}
)
FAILED_JOB_STATUSES = frozenset({JobStatus.FAILED, JobStatus.PARTIAL})

SKIP_REASON_LABELS: Mapping[str, str] = {
    REASON_COOLDOWN: "Cooldown after a recent run",
    REASON_BACKOFF: "Backing off after failed runs",
    REASON_BUDGET_CAP: "Over the nightly budget",
    REASON_STALE_STATS: "Stats too old to plan from",
    REASON_EXCLUDED: "Not eligible for maintenance",
}


def last_job_status_expr() -> ColumnElement[Any]:
    """Status of the table's latest job. An unfinished job sorts first, so it wins."""
    return (
        select(MaintenanceJob.status)
        .where(MaintenanceJob.table_id == Table.id)
        .order_by(MaintenanceJob.finished_at.desc().nulls_first())
        .limit(1)
        .correlate(Table)
        .scalar_subquery()
    )


def _latest_selection(column: Any) -> ColumnElement[Any]:
    return (
        select(column)
        .where(SkipEvaluation.table_id == Table.id)
        .order_by(SkipEvaluation.evaluated_at.desc())
        .limit(1)
        .correlate(Table)
        .scalar_subquery()
    )


def last_selection_stage_expr() -> ColumnElement[Any]:
    return _latest_selection(SkipEvaluation.stage)


def last_selection_reason_expr() -> ColumnElement[Any]:
    return _latest_selection(SkipEvaluation.reason)


def score_delta_expr(now: datetime | None = None) -> ColumnElement[Any]:
    """Current score minus the last recorded score at least SCORE_DELTA_DAYS ago."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=SCORE_DELTA_DAYS)
    past = (
        select(TableStatsHistory.overall_score)
        .where(TableStatsHistory.table_id == Table.id, TableStatsHistory.scan_at <= cutoff)
        .order_by(TableStatsHistory.scan_at.desc())
        .limit(1)
        .correlate(Table)
        .scalar_subquery()
    )
    return TableStats.overall_score - past


COMPUTED_FIELDS: Mapping[str, Callable[[], ColumnElement[Any]]] = {
    "last_job_status": last_job_status_expr,
    "last_selection_stage": last_selection_stage_expr,
    "last_selection_reason": last_selection_reason_expr,
    "score_delta_7d": score_delta_expr,
}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class StateInputs:
    is_active: bool
    has_stats: bool
    needs_work: bool
    scan_failures: int
    scan_error_kind: str | None
    excluded: bool
    retry_after: datetime | None
    missing_metadata_reason: str | None
    active_job_status: str | None
    last_finished_status: str | None
    selection_stage: str | None
    selection_reason: str | None


def derive_state(inputs: StateInputs, *, now: datetime) -> tuple[TableMaintenanceState, str | None]:
    """One state per table, most actionable first. Mirrors the batch selection's own gates."""
    if not inputs.is_active:
        return "inactive", "Table is no longer in the catalog"
    if inputs.active_job_status == JobStatus.RUNNING:
        return "running", None
    if inputs.active_job_status in ACTIVE_JOB_STATUSES:
        reason = {
            JobStatus.ADMITTED: "Admitted, waiting for a worker",
            JobStatus.DEFERRED: "Deferred by the governor",
        }.get(JobStatus(inputs.active_job_status), "Waiting for engine capacity")
        return "queued", reason
    if inputs.excluded:
        return "excluded", "Excluded by an operator"
    retry_after = _as_utc(inputs.retry_after)
    if retry_after is not None and retry_after > now:
        reason = inputs.missing_metadata_reason or "Metadata missing in storage"
        return "held", f"{reason}; retry after {retry_after:%b %d %H:%M} UTC"
    if inputs.scan_error_kind is not None or inputs.scan_failures > 0:
        kind = "unreadable" if inputs.scan_error_kind == "unreadable" else "failing"
        return "scan_failing", f"Last scan {kind}"
    if not inputs.has_stats:
        return "not_scanned", "Waiting for its first scan"
    if inputs.last_finished_status in FAILED_JOB_STATUSES:
        label = (
            "Last run partly failed"
            if inputs.last_finished_status == "partial"
            else "Last run failed"
        )
        return "failed", label
    if (
        inputs.needs_work
        and inputs.selection_stage in {STAGE_NEEDS_WORK, STAGE_ELIGIBLE}
        and inputs.selection_reason
    ):
        return "skipped", SKIP_REASON_LABELS.get(inputs.selection_reason, inputs.selection_reason)
    if inputs.needs_work:
        return "needs_work", "Waiting for the next nightly run"
    return "healthy", None


def _latest_rows(
    session: Session,
    stmt: Any,
    *,
    partition: Any,
    order: Any,
) -> list[Any]:
    ranked = stmt.add_columns(
        func.row_number().over(partition_by=partition, order_by=order).label("rn")
    ).subquery()
    return list(session.execute(select(ranked).where(ranked.c.rn == 1)).all())


def _job_ref(row: Any) -> TableJobRef:
    return TableJobRef(
        run_id=row.run_id,
        job_id=row.id,
        status=row.status,
        trigger=row.trigger,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def maintenance_summaries(
    session: Session, tables: Sequence[Table], *, now: datetime | None = None
) -> dict[uuid.UUID, TableMaintenanceRead]:
    if not tables:
        return {}
    current = now or datetime.now(UTC)
    ids = [table.id for table in tables]
    job_columns = select(
        MaintenanceJob.id,
        MaintenanceJob.table_id,
        MaintenanceJob.run_id,
        MaintenanceJob.status,
        MaintenanceJob.started_at,
        MaintenanceJob.finished_at,
        MaintenanceRun.trigger,
    ).join(MaintenanceRun, MaintenanceRun.id == MaintenanceJob.run_id)
    latest_jobs = {
        row.table_id: row
        for row in _latest_rows(
            session,
            job_columns.where(MaintenanceJob.table_id.in_(ids)),
            partition=MaintenanceJob.table_id,
            order=MaintenanceJob.finished_at.desc().nulls_first(),
        )
    }
    finished_jobs = {
        row.table_id: row
        for row in _latest_rows(
            session,
            job_columns.where(
                MaintenanceJob.table_id.in_(ids), MaintenanceJob.finished_at.is_not(None)
            ),
            partition=MaintenanceJob.table_id,
            order=MaintenanceJob.finished_at.desc(),
        )
    }
    selections = {
        row.table_id: row
        for row in _latest_rows(
            session,
            select(
                SkipEvaluation.table_id,
                SkipEvaluation.stage,
                SkipEvaluation.reason,
                SkipEvaluation.evaluated_at,
            ).where(SkipEvaluation.table_id.in_(ids)),
            partition=SkipEvaluation.table_id,
            order=SkipEvaluation.evaluated_at.desc(),
        )
    }
    cutoff = current - timedelta(days=SCORE_DELTA_DAYS)
    past_scores = {
        row.table_id: row.overall_score
        for row in _latest_rows(
            session,
            select(TableStatsHistory.table_id, TableStatsHistory.overall_score).where(
                TableStatsHistory.table_id.in_(ids), TableStatsHistory.scan_at <= cutoff
            ),
            partition=TableStatsHistory.table_id,
            order=TableStatsHistory.scan_at.desc(),
        )
    }
    workflows = {
        row.table_id: (row.name, row.pinned)
        for row in session.execute(
            select(TableAssignment.table_id, Preset.name, TableAssignment.pinned)
            .join(Preset, Preset.id == TableAssignment.preset_id)
            .where(TableAssignment.table_id.in_(ids))
        )
    }
    excluded = excluded_table_ids(session, ids)

    out: dict[uuid.UUID, TableMaintenanceRead] = {}
    for table in tables:
        stats = table.stats
        latest = latest_jobs.get(table.id)
        finished = finished_jobs.get(table.id)
        selection = selections.get(table.id)
        active = latest if latest is not None and latest.status in ACTIVE_JOB_STATUSES else None
        state, reason = derive_state(
            StateInputs(
                is_active=table.is_active,
                has_stats=stats is not None,
                needs_work=bool(stats and stats.needs_compaction),
                scan_failures=table.consecutive_scan_failures or 0,
                scan_error_kind=table.last_scan_error_kind,
                excluded=table.id in excluded,
                retry_after=table.maintenance_retry_after,
                missing_metadata_reason=table.maintenance_missing_metadata_reason,
                active_job_status=active.status if active is not None else None,
                last_finished_status=finished.status if finished is not None else None,
                selection_stage=selection.stage if selection is not None else None,
                selection_reason=selection.reason if selection is not None else None,
            ),
            now=current,
        )
        past = past_scores.get(table.id)
        workflow = workflows.get(table.id)
        out[table.id] = TableMaintenanceRead(
            state=state,
            reason=reason,
            workflow_name=workflow[0] if workflow else None,
            workflow_pinned=bool(workflow and workflow[1]),
            active_run=_job_ref(active) if active is not None else None,
            last_run=_job_ref(finished) if finished is not None else None,
            last_selection=(
                TableSelectionRef(
                    stage=selection.stage,
                    reason=selection.reason,
                    evaluated_at=selection.evaluated_at,
                )
                if selection is not None
                else None
            ),
            score_delta_7d=(
                stats.overall_score - past
                if stats is not None and past is not None and stats.overall_score is not None
                else None
            ),
        )
    return out


# --- Fleet funnel ---------------------------------------------------------------------------

_JOB_RANK = {
    JobStatus.SKIPPED: 1,
    JobStatus.SUCCEEDED: 2,
    JobStatus.PARTIAL: 3,
    JobStatus.QUEUED: 4,
    JobStatus.ADMITTED: 4,
    JobStatus.DEFERRED: 4,
    JobStatus.RUNNING: 5,
    JobStatus.FAILED: 6,
}
_RANK_OUTCOME = {
    1: "not_run",
    2: "succeeded",
    3: "failed",
    4: "in_progress",
    5: "in_progress",
    6: "failed",
}

NODE_LABELS: Mapping[str, str] = {
    "active": "Active tables",
    "scan_failing": "Scan failing",
    "not_scanned": "Not scanned yet",
    "scanned": "Scanned",
    "not_evaluated": "Not evaluated",
    "no_work": "No work needed",
    "needs_work": "Needs work",
    "skip_cooldown": "Cooldown",
    "skip_backoff": "Backing off",
    "skip_budget_cap": "Over budget",
    "skip_stale_stats": "Stale stats",
    "skip_excluded": "Not eligible",
    "selected": "Selected",
    "succeeded": "Succeeded",
    "failed": "Failed",
    "in_progress": "In progress",
    "not_run": "Not run",
}

# Lets a Sankey node open the explorer on the tables it counts (current state, not the run's).
NODE_VIEWS: Mapping[str, str] = {
    "scan_failing": "scan-issues",
    "not_scanned": "scan-issues",
    "needs_work": "needs-work",
    "skip_cooldown": "skipped",
    "skip_backoff": "skipped",
    "skip_budget_cap": "skipped",
    "skip_stale_stats": "skipped",
    "skip_excluded": "skipped",
    "failed": "failed",
    "in_progress": "in-progress",
}


@dataclass(frozen=True)
class FunnelRow:
    scan_bucket: str
    stage: str | None
    reason: str | None
    job_rank: int | None
    count: int


def funnel_path(row: FunnelRow) -> list[str]:
    """Node ids from root to leaf for one group of tables."""
    if row.scan_bucket != "scanned":
        return ["active", row.scan_bucket]
    path = ["active", "scanned"]
    if row.stage is None:
        return [*path, "not_evaluated"]
    if row.stage == STAGE_EVALUATED:
        return [*path, "no_work"]
    path.append("needs_work")
    if row.stage == STAGE_ELIGIBLE and row.reason == REASON_BUDGET_CAP:
        return [*path, "skip_budget_cap"]
    if row.stage == STAGE_NEEDS_WORK:
        # `row.reason or REASON_EXCLUDED` is a defensive fallback, not a live case: the only
        # producer of STAGE_NEEDS_WORK rows (selection._exclusion_rows -> _exclusion_reason)
        # always returns a non-empty reason string, falling back to REASON_EXCLUDED itself.
        # If a future caller ever writes a STAGE_NEEDS_WORK row with an empty reason, this
        # silently mislabels it as "excluded" in the funnel -- worth revisiting then.
        return [*path, f"skip_{row.reason or REASON_EXCLUDED}"]
    path.append("selected")
    return [*path, _RANK_OUTCOME.get(row.job_rank or 0, "not_run")]


def build_funnel(rows: Iterable[FunnelRow]) -> tuple[list[FunnelNode], list[FunnelLink]]:
    counts: dict[str, int] = defaultdict(int)
    edges: dict[tuple[str, str], int] = defaultdict(int)
    order: list[str] = []
    for row in rows:
        if row.count <= 0:
            continue
        path = funnel_path(row)
        for node in path:
            if node not in counts:
                order.append(node)
            counts[node] += row.count
        for source, target in zip(path, path[1:], strict=False):
            edges[(source, target)] += row.count
    rank = {node: index for index, node in enumerate(NODE_LABELS)}
    order.sort(key=lambda node: rank.get(node, len(rank)))
    nodes = [
        FunnelNode(
            id=node,
            label=NODE_LABELS.get(node, node.replace("_", " ").capitalize()),
            count=counts[node],
            view=NODE_VIEWS.get(node),
        )
        for node in order
    ]
    links = [
        FunnelLink(source=source, target=target, value=value)
        for (source, target), value in edges.items()
    ]
    return nodes, links


def latest_batch_run(session: Session) -> MaintenanceRun | None:
    runs = session.scalars(
        select(MaintenanceRun).order_by(MaintenanceRun.created_at.desc()).limit(20)
    ).all()
    for run in runs:
        summary = run.summary if isinstance(run.summary, dict) else None
        if summary and summary.get("pipeline") == "batch":
            return run
    return None


def tables_funnel(session: Session, *, tenant_id: uuid.UUID) -> TablesFunnelResponse:
    run = latest_batch_run(session)
    scan_bucket = case(
        (
            or_(Table.last_scan_error_kind.is_not(None), Table.consecutive_scan_failures > 0),
            "scan_failing",
        ),
        (TableStats.table_id.is_(None), "not_scanned"),
        else_="scanned",
    ).label("scan_bucket")
    run_id = run.id if run is not None else None
    job_rank_whens = [(MaintenanceJob.status == status, rank) for status, rank in _JOB_RANK.items()]
    jobs = (
        select(
            MaintenanceJob.table_id.label("table_id"),
            func.max(case(*job_rank_whens, else_=0)).label("job_rank"),
        )
        .where(MaintenanceJob.run_id == run_id)
        .group_by(MaintenanceJob.table_id)
        .subquery()
    )
    evaluations = (
        select(SkipEvaluation.table_id, SkipEvaluation.stage, SkipEvaluation.reason)
        .where(SkipEvaluation.run_id == run_id)
        .subquery()
    )
    stmt = (
        select(
            scan_bucket,
            evaluations.c.stage,
            evaluations.c.reason,
            jobs.c.job_rank,
            func.count().label("table_count"),
        )
        .select_from(Table)
        .outerjoin(TableStats, TableStats.table_id == Table.id)
        .outerjoin(evaluations, evaluations.c.table_id == Table.id)
        .outerjoin(jobs, jobs.c.table_id == Table.id)
        .where(Table.is_active.is_(True))
        .group_by(scan_bucket, evaluations.c.stage, evaluations.c.reason, jobs.c.job_rank)
    )
    stmt = scope_by_tenant(stmt, Table, tenant_id)
    rows = [
        FunnelRow(
            scan_bucket=str(row.scan_bucket),
            stage=row.stage,
            reason=row.reason,
            job_rank=int(row.job_rank) if row.job_rank is not None else None,
            count=int(row.table_count),
        )
        for row in session.execute(stmt)
    ]
    nodes, links = build_funnel(rows)
    return TablesFunnelResponse(
        run_id=run_id,
        run_status=run.status if run is not None else None,
        run_started_at=run.created_at if run is not None else None,
        run_finished_at=run.finished_at if run is not None else None,
        total_active=sum(row.count for row in rows),
        nodes=nodes,
        links=links,
    )
