"""Per-table maintenance: nightly eligibility and one-off operation runs.

``GET /api/v1/tables/{table_id}/maintenance-status`` (viewer). Reasons come from the
per-table predicates in ``load_candidates``, ``is_eligible``, and ``scan_retry_due_at``.

``POST /api/v1/tables/{table_id}/maintenance/operations`` (operator) runs chosen
operations on this one table through the regular maintenance-run path.

``GET /api/v1/tables/{table_id}/spec-migration`` (viewer) reports what is still written
under an older partition spec, and ``POST .../spec-migration/approval-request`` (operator)
asks for the approval a migration run needs (#289).
The router is not mounted here; ``app.py`` is owned by other work.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.api.deps import (
    CurrentPrincipal,
    SessionDep,
    require_operator,
    require_viewer,
)
from cryo.api.schemas import MaintenanceRunRequest, MaintenanceRunResponse
from cryo.config import get_settings
from cryo.db.models import AuditLog, Destination, MaintenanceJob, Source, Table, TableStats
from cryo.db.repositories.exclusions import excluded_table_ids
from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.maintenance.selection import (
    DEFAULT_MIN_DELETE_FILE_PCT,
    BlockingReason,
    MaintenanceEligibilityFacts,
    job_starts_cooldown,
    maintenance_blocking_reasons,
    nightly_selection_eligible,
)
from cryo.scanner.persistence import scan_retry_due_at

router = APIRouter(prefix="/api/v1", tags=["tables"])

LAST_ACTIONS_NOTE = (
    "No existing repository function returns the latest audit or job row per operation "
    "without an unbounded scan. table_audit is bounded by limit but does not group by "
    "operation, so last_actions is empty."
)
NEXT_NIGHTLY_NOTE = (
    "The nightly cron is Helm maintenance.schedule "
    "(deploy/helm/cryo/templates/maintenance-cronjob.yaml), not an application setting. "
    "next_nightly_at is null because it is not derivable without new config."
)
ELIGIBILITY_BASIS = (
    "Nightly batch: load_candidates with cooldown applied, then is_eligible "
    "(rank_candidates require_eligible=True, min_delete_file_pct default). "
    "eligible is false only when a blocking reason has blocks_nightly_selection. "
    "scan_retry_backoff comes from scan_retry_due_at and does not by itself fail selection. "
    "An ad-hoc run that passes table_ids skips cooldown and the eligibility filter."
)
OVER_BUDGET_WHERE = "cryo.maintenance.selection.fit_budget, applied by BatchMaintenance._select"
OVER_BUDGET_WHY = (
    "Not a load_candidates predicate. It lives in fit_budget, which "
    "BatchMaintenance._select applies after ranking. A table is over budget only "
    "relative to the run's remaining GB·hr, so this endpoint does not invent a per-table rule."
)


class BlockingReasonRead(BaseModel):
    code: str
    summary: str
    predicate: str
    blocks_nightly_selection: bool
    until: datetime | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class OmittedGateRead(BaseModel):
    code: str
    where: str
    why: str


class MaintenanceThresholdsRead(BaseModel):
    cooldown_hours: float
    max_stats_age_days: float
    min_delete_file_pct: float


class MaintenanceStatusRead(BaseModel):
    table_id: uuid.UUID
    eligible: bool
    evaluated_at: datetime
    eligibility_basis: str
    blocking_reasons: list[BlockingReasonRead]
    thresholds: MaintenanceThresholdsRead
    last_actions: list[dict[str, Any]]
    last_actions_note: str
    next_nightly_at: datetime | None
    next_nightly_note: str
    omitted: list[OmittedGateRead]


def _omitted_gates() -> list[OmittedGateRead]:
    return [
        OmittedGateRead(
            code="over_budget_last_run",
            where=OVER_BUDGET_WHERE,
            why=OVER_BUDGET_WHY,
        )
    ]


def _latest_successful_finished_at(session: Session, table_id: uuid.UUID) -> datetime | None:
    return session.scalar(
        select(MaintenanceJob.finished_at)
        .where(
            MaintenanceJob.table_id == table_id,
            job_starts_cooldown(),
            MaintenanceJob.finished_at.is_not(None),
        )
        .order_by(MaintenanceJob.finished_at.desc())
        .limit(1)
    )


def _load_facts(session: Session, table: Table) -> MaintenanceEligibilityFacts:
    source = session.execute(
        select(Source.enabled, Source.maintenance_schema_names).where(Source.id == table.source_id)
    ).one_or_none()
    stats = session.execute(
        select(
            TableStats.scan_at,
            TableStats.needs_compaction,
            TableStats.delete_file_pct,
        ).where(TableStats.table_id == table.id)
    ).one_or_none()
    return MaintenanceEligibilityFacts(
        is_active=table.is_active is True,
        source_enabled=bool(source.enabled) if source is not None else False,
        source_present=source is not None,
        last_scan_error_kind=table.last_scan_error_kind,
        maintenance_retry_after=table.maintenance_retry_after,
        stats_present=stats is not None,
        scan_at=stats.scan_at if stats is not None else None,
        schema_name=table.schema_name,
        maintenance_schema_names=(source.maintenance_schema_names if source is not None else None),
        needs_compaction=bool(stats.needs_compaction) if stats is not None else False,
        delete_file_pct=float(stats.delete_file_pct or 0.0) if stats is not None else 0.0,
        last_successful_finished_at=_latest_successful_finished_at(session, table.id),
        consecutive_scan_failures=table.consecutive_scan_failures or 0,
        last_scan_error_at=table.last_scan_error_at,
        scan_retry_due_at=scan_retry_due_at(table),
        operator_excluded=bool(excluded_table_ids(session, [table.id])),
    )


def _reason_read(reason: BlockingReason) -> BlockingReasonRead:
    return BlockingReasonRead(
        code=reason.code.value,
        summary=reason.summary,
        predicate=reason.predicate.value,
        blocks_nightly_selection=reason.blocks_nightly_selection,
        until=reason.until,
        values=reason.values,
    )


@router.get(
    "/tables/{table_id}/maintenance-status",
    response_model=MaintenanceStatusRead,
    dependencies=[Depends(require_viewer)],
)
def get_table_maintenance_status(
    table_id: uuid.UUID,
    session: SessionDep,
) -> MaintenanceStatusRead:
    """Why this table would or would not enter the nightly batch."""
    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")

    settings = get_settings()
    now = datetime.now(UTC)
    cooldown_hours = float(settings.maintenance_cooldown_hours)
    max_stats_age_days = float(settings.maintenance_max_stats_age_days)
    reasons = maintenance_blocking_reasons(
        _load_facts(session, table),
        now=now,
        cooldown_hours=cooldown_hours,
        max_stats_age_days=max_stats_age_days,
    )
    return MaintenanceStatusRead(
        table_id=table_id,
        eligible=nightly_selection_eligible(reasons),
        evaluated_at=now,
        eligibility_basis=ELIGIBILITY_BASIS,
        blocking_reasons=[_reason_read(reason) for reason in reasons],
        thresholds=MaintenanceThresholdsRead(
            cooldown_hours=cooldown_hours,
            max_stats_age_days=max_stats_age_days,
            min_delete_file_pct=DEFAULT_MIN_DELETE_FILE_PCT,
        ),
        last_actions=[],
        last_actions_note=LAST_ACTIONS_NOTE,
        next_nightly_at=None,
        next_nightly_note=NEXT_NIGHTLY_NOTE,
        omitted=_omitted_gates(),
    )


_OPERATION_RUN_OPS = {
    OperationType.OPTIMIZE,
    OperationType.OPTIMIZE_MANIFESTS,
    OperationType.EXPIRE_SNAPSHOTS,
    OperationType.REMOVE_ORPHAN_FILES,
    OperationType.MIGRATE_PARTITION_SPEC,
}


class TableOperationRunRequest(BaseModel):
    operations: list[OperationType] = Field(min_length=1)
    trigger: str | None = "web-table-operation"
    force: bool = False


def mode_for_operations(operations: set[OperationType]) -> Mode:
    """The lightest planner mode that plans every requested operation.

    build_plan gates by mode: light plans only expire_snapshots, standard adds compaction,
    and remove_orphan_files/optimize_manifests are deep-only. skip_operations then drops
    whatever the mode planned that wasn't asked for, so a lighter mode never runs less
    than requested and never plans work (an S3 LIST for orphans) that it will throw away.
    """
    if operations == {OperationType.OPTIMIZE_MANIFESTS}:
        return Mode.MANIFEST_ONLY
    if operations & {OperationType.REMOVE_ORPHAN_FILES, OperationType.OPTIMIZE_MANIFESTS}:
        return Mode.DEEP
    if operations & {OperationType.OPTIMIZE, OperationType.MIGRATE_PARTITION_SPEC}:
        return Mode.STANDARD
    return Mode.LIGHT


@router.post(
    "/tables/{table_id}/maintenance/operations",
    response_model=MaintenanceRunResponse,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
def run_table_operations(
    table_id: uuid.UUID,
    payload: TableOperationRunRequest,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> MaintenanceRunResponse:
    """Run chosen maintenance operations on one table, now.

    The table's effective workflow supplies the destination and parameters (retention,
    orphan age, chunking). Unlike /maintenance/run, the operations need not be in the
    workflow's list: this path targets exactly one table the operator is looking at, so
    "expire snapshots on this table" works even when its workflow only compacts. ABAC,
    the plan verifier, protected snapshots and the shared-location orphan gate all still
    apply through start_maintenance_run and the planner.
    """
    from cryo.db.models import Preset
    from cryo.db.repositories import presets as preset_repo
    from cryo.maintenance.run_service import start_maintenance_run

    requested = set(payload.operations)
    unsupported = requested - _OPERATION_RUN_OPS
    if unsupported:
        names = ", ".join(sorted(op.value for op in unsupported))
        raise HTTPException(status_code=400, detail=f"Not runnable from a table: {names}")
    table = preset_repo.get_table_with_stats(session, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    if OperationType.MIGRATE_PARTITION_SPEC in requested:
        approval = _migration_approval(session, table_id)
        if approval is None or approval.status != "approved":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Migrating to the current partition spec needs an approved request. "
                    "Request it, then approve it under Decisions, Approvals."
                ),
            )
    effective = preset_repo.resolve_effective_preset(session, table)
    if effective is None:
        raise HTTPException(
            status_code=409,
            detail="Assign a workflow first; it supplies the engine and retention settings.",
        )
    preset = session.get(Preset, uuid.UUID(effective.preset.id))
    if preset is None:
        raise HTTPException(status_code=409, detail="The table's workflow no longer exists")
    request = MaintenanceRunRequest(
        table_ids=[table_id],
        destination_id=preset.destination_id,
        preset_id=preset.id,
        mode=mode_for_operations(requested),
        trigger=payload.trigger,
        operations=[op.value for op in payload.operations],
        force=payload.force,
    )
    return start_maintenance_run(session, request, principal, allow_operations_outside_preset=True)


class SpecMigrationApprovalRead(BaseModel):
    request_id: uuid.UUID
    status: str
    requested_by: str
    approved_by: str | None = None
    expires_at: datetime | None = None


class SpecMigrationLastRunRead(BaseModel):
    status: str
    at: datetime
    job_id: uuid.UUID | None = None
    detail: str | None = None


class SpecMigrationRead(BaseModel):
    """What is still written under an older partition spec, from the last scan."""

    table_id: uuid.UUID
    scanned_at: datetime | None
    recorded: bool
    default_spec_id: int | None
    old_spec_partitions: int
    old_spec_files: int
    old_spec_bytes: int
    # Old-spec partitions when the latest migration approval was requested: the baseline for
    # progress. None before the first request.
    baseline_old_spec_partitions: int | None
    destination_engine: str | None
    engine_supported: bool
    engine_note: str | None
    approval: SpecMigrationApprovalRead | None
    last_run: SpecMigrationLastRunRead | None


def _migration_approval(session: Session, table_id: uuid.UUID) -> SpecMigrationApprovalRead | None:
    """The live (pending or approved, unexecuted, unexpired) migration request, if any."""
    from cryo.workflows.approvals import ApprovalRequest, ApprovalStatus

    request = session.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.table_id == table_id,
            ApprovalRequest.operation_type == OperationType.MIGRATE_PARTITION_SPEC.value,
            ApprovalRequest.status.in_(
                [ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value]
            ),
            ApprovalRequest.executed_at.is_(None),
            ApprovalRequest.expires_at > datetime.now(UTC),
        )
        .order_by(ApprovalRequest.created_at.desc())
        .limit(1)
    )
    if request is None:
        return None
    return SpecMigrationApprovalRead(
        request_id=request.id,
        status=str(request.status),
        requested_by=request.requested_by,
        approved_by=request.approved_by,
        expires_at=request.expires_at,
    )


def _migration_baseline(session: Session, table_id: uuid.UUID) -> int | None:
    from cryo.workflows.approvals import ApprovalRequest

    preview = session.scalar(
        select(ApprovalRequest.impact_preview)
        .where(
            ApprovalRequest.table_id == table_id,
            ApprovalRequest.operation_type == OperationType.MIGRATE_PARTITION_SPEC.value,
        )
        .order_by(ApprovalRequest.created_at.desc())
        .limit(1)
    )
    value = preview.get("old_spec_partitions") if isinstance(preview, dict) else None
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _migration_last_run(session: Session, table_id: uuid.UUID) -> SpecMigrationLastRunRead | None:
    row = session.scalar(
        select(AuditLog)
        .where(
            AuditLog.table_id == table_id,
            AuditLog.action == OperationType.MIGRATE_PARTITION_SPEC.value,
        )
        .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    return SpecMigrationLastRunRead(
        status=row.status, at=row.ts, job_id=row.job_id, detail=row.detail
    )


def _migration_engine(session: Session, table: Table) -> tuple[str | None, bool, str | None]:
    from cryo.db.repositories import presets as preset_repo

    effective = preset_repo.resolve_effective_preset(session, table)
    if effective is None:
        return None, False, "Assign a workflow first; it supplies the engine."
    from cryo.db.models import Preset

    preset = session.get(Preset, uuid.UUID(effective.preset.id))
    destination = (
        session.get(Destination, preset.destination_id)
        if preset is not None and preset.destination_id is not None
        else None
    )
    if destination is None:
        return None, False, "The table's workflow has no destination."
    engine = str(destination.engine).lower()
    if engine == EngineType.TRINO.value:
        return (
            engine,
            False,
            "Trino cannot filter partitions of an older spec. Route this table to a Spark "
            "destination to migrate it.",
        )
    if engine == EngineType.NATIVE.value:
        return (
            engine,
            True,
            "The native engine runs the migration on its Spark fallback destination.",
        )
    return engine, True, None


@router.get(
    "/tables/{table_id}/spec-migration",
    response_model=SpecMigrationRead,
    dependencies=[Depends(require_viewer)],
)
def get_table_spec_migration(table_id: uuid.UUID, session: SessionDep) -> SpecMigrationRead:
    """Old-spec partitions left to migrate, the approval state, and the last migration run."""
    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    stats = session.get(TableStats, table_id)
    migration = ((stats.raw or {}) if stats is not None else {}).get("spec_migration")
    recorded = isinstance(migration, dict) and migration.get("default_spec_id") is not None
    values: dict[str, Any] = migration if recorded and isinstance(migration, dict) else {}
    engine, supported, note = _migration_engine(session, table)
    return SpecMigrationRead(
        table_id=table_id,
        scanned_at=stats.scan_at if stats is not None else None,
        recorded=recorded,
        default_spec_id=int(values["default_spec_id"]) if recorded else None,
        old_spec_partitions=int(values.get("old_spec_partitions") or 0),
        old_spec_files=int(values.get("old_spec_files") or 0),
        old_spec_bytes=int(values.get("old_spec_bytes") or 0),
        baseline_old_spec_partitions=_migration_baseline(session, table_id),
        destination_engine=engine,
        engine_supported=supported,
        engine_note=note,
        approval=_migration_approval(session, table_id),
        last_run=_migration_last_run(session, table_id),
    )


class SpecMigrationApprovalRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


@router.post(
    "/tables/{table_id}/spec-migration/approval-request",
    response_model=SpecMigrationApprovalRead,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
def request_spec_migration_approval(
    table_id: uuid.UUID,
    payload: SpecMigrationApprovalRequest,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> SpecMigrationApprovalRead:
    """Ask for approval to migrate this table; returns the live request if one exists."""
    from cryo.workflows.approvals import (
        ApprovalRequestData,
        create_approval_request,
        estimate_impact,
    )

    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    stats = session.get(TableStats, table_id)
    migration = ((stats.raw or {}) if stats is not None else {}).get("spec_migration") or {}
    if migration.get("default_spec_id") is None:
        raise HTTPException(
            status_code=409,
            detail="Rescan the table first: the last scan did not record its partition specs.",
        )
    if int(migration.get("old_spec_partitions") or 0) == 0:
        raise HTTPException(
            status_code=409,
            detail="Every data file is already written under the default partition spec.",
        )
    existing = _migration_approval(session, table_id)
    if existing is not None:
        return existing
    operation = OperationType.MIGRATE_PARTITION_SPEC
    create_approval_request(
        session,
        ApprovalRequestData(
            table_id=table_id,
            operation_type=operation,
            requested_by=principal.subject,
            request_reason=payload.reason or "Migrate old-spec partitions to the current spec",
            impact_preview=estimate_impact(session, table_id, operation, {}),
            operation_params={},
        ),
    )
    created = _migration_approval(session, table_id)
    if created is None:  # pragma: no cover - the request was just committed
        raise HTTPException(status_code=500, detail="Approval request was not recorded")
    return created
