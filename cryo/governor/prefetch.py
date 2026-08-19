"""Batch DB reads for governor admission waves."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.compliance.pii_gate import maintenance_blocked_for_untagged_pii
from cryo.db.models import MaintenanceControlState, Table, TableStats
from cryo.workflows.approvals import ApprovalRequest as ApprovalRequestModel
from cryo.workflows.approvals import ApprovalStatus, _parse_operation_type, requires_approval


def prefetch_table_stats(
    session: Session,
    table_ids: set[uuid.UUID],
) -> dict[uuid.UUID, TableStats]:
    if not table_ids:
        return {}
    rows = session.scalars(select(TableStats).where(TableStats.table_id.in_(table_ids)))
    return {row.table_id: row for row in rows}


def prefetch_paused_table_ids(
    session: Session,
    table_ids: set[uuid.UUID],
    *,
    destination_id: str,
) -> set[uuid.UUID]:
    if not table_ids:
        return set()
    global_or_destination_pause = session.scalar(
        select(MaintenanceControlState.id).where(
            MaintenanceControlState.paused.is_(True),
            (
                (MaintenanceControlState.scope_type == "global")
                & (MaintenanceControlState.scope_id == "global")
            )
            | (
                (MaintenanceControlState.scope_type == "destination")
                & (MaintenanceControlState.scope_id == destination_id)
            ),
        )
    )
    if global_or_destination_pause is not None:
        return set(table_ids)
    paused_tenants = list(
        session.scalars(
            select(MaintenanceControlState.scope_id).where(
                MaintenanceControlState.paused.is_(True),
                MaintenanceControlState.scope_type == "tenant",
            )
        )
    )
    if not paused_tenants:
        return set()
    tenant_ids: list[uuid.UUID] = []
    for raw in paused_tenants:
        try:
            tenant_ids.append(uuid.UUID(raw))
        except ValueError:
            continue
    if not tenant_ids:
        return set()
    return set(
        session.scalars(
            select(Table.id).where(
                Table.id.in_(table_ids),
                Table.tenant_id.in_(tenant_ids),
            )
        )
    )


def prefetch_paused_destination_ids(
    session: Session,
    destination_ids: set[str],
) -> set[str]:
    if not destination_ids:
        return set()
    return set(
        session.scalars(
            select(MaintenanceControlState.scope_id).where(
                MaintenanceControlState.paused.is_(True),
                MaintenanceControlState.scope_type == "destination",
                MaintenanceControlState.scope_id.in_(destination_ids),
            )
        )
    )


def prefetch_approved_operation_keys(
    session: Session,
    table_ids: set[uuid.UUID],
) -> dict[tuple[uuid.UUID, str], list[dict[str, Any]]]:
    """Approved operations per table, still valid for admission.

    BUGFIX 2026-08-19: the two filters this query used to apply --
    ``executed_at.is_(None)`` and ``expires_at.is_not(None) AND expires_at > now()`` --
    together made every approved row in prod permanently unmatchable. Verified live: of
    10,460 ``status='approved'`` rows, 0 satisfied both conditions -- every single one had
    ``expires_at IS NULL`` (the model's own ``create_approval_request`` always sets a real
    expiry via ``auto_expire_hours``, so a NULL here is a deliberately-created standing grant,
    not an oversight; the previous query's ``is_not(None)`` treated SQL NULL's natural "no
    constraint" meaning as "always excluded" instead), and 8,547 already had a non-NULL
    ``executed_at`` from a prior run. That second part was also wrong on its own terms:
    ``expire_snapshots``/``remove_orphan_files`` are operations meant to run repeatedly
    forever, not once -- gating re-use on "never yet executed" turns every approval into a
    silent single-use grant the moment the governor's own JobWatcher marks it executed,
    which is exactly the class of table this query exists to keep unblocked. Net effect: the
    entire fleet-wide approval-gated maintenance path (the default `requires_approval` set
    for these two operations) had been non-functional since the safety-kernel commit that
    introduced it (eee1788, 2026-08-05) -- confirmed live via a stuck cron run whose 2,173
    queued jobs were deferring 100% of the time with reason "destructive operation requires
    approval" despite a real, permanent, already-successfully-used approval existing for the
    table in question.

    Correct semantics: a row counts as still-approved when its ``expires_at`` is either NULL
    (no expiry constraint -- a standing grant) or in the future, regardless of whether it has
    already been executed before -- the same approval authorizes every future run of that
    operation on that table until it expires or is revoked, not just the first.
    """
    if not table_ids:
        return {}
    now = datetime.now(UTC)
    stmt = select(ApprovalRequestModel).where(
        ApprovalRequestModel.table_id.in_(table_ids),
        ApprovalRequestModel.status == ApprovalStatus.APPROVED,
        (ApprovalRequestModel.expires_at.is_(None)) | (ApprovalRequestModel.expires_at > now),
    )
    result: dict[tuple[uuid.UUID, str], list[dict[str, Any]]] = {}
    for row in session.scalars(stmt):
        key = (row.table_id, row.operation_type)
        if key not in result:
            result[key] = []
        result[key].append(row.operation_params or {})
    return result


def batch_job_requires_approval(
    table_id: uuid.UUID,
    plan_operations: list[dict[str, Any]] | None,
    approved_keys: dict[tuple[uuid.UUID, str], list[dict[str, Any]]],
) -> bool:
    if not plan_operations:
        return False
    from cryo.workflows.approvals import _is_parameter_safe

    for op in plan_operations:
        if not isinstance(op, dict):
            continue
        op_type = _parse_operation_type(str(op.get("type", "")))
        if op_type is None or not requires_approval(op_type):
            continue
        key = (table_id, op_type.value)
        if key not in approved_keys:
            return True
        op_params = op.get("params") or {}
        approved_param_sets = approved_keys[key]
        if not any(_is_parameter_safe(op_params, app_params) for app_params in approved_param_sets):
            return True
    return False


def batch_pii_block_reason(
    session: Session,
    table_id: uuid.UUID,
    plan_operations: list[dict[str, Any]] | None,
    *,
    job_params: Mapping[str, Any] | None,
    stats_cache: dict[uuid.UUID, TableStats],
) -> str | None:
    stats = stats_cache.get(table_id)
    if stats is not None:
        session.merge(stats)
    return maintenance_blocked_for_untagged_pii(
        session,
        table_id,
        plan_operations,
        job_params=job_params,
    )
