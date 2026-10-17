from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from cryo.api.deps import SessionDep, require_operator, require_viewer
from cryo.api.security import Principal, Role
from cryo.db.models import Table
from cryo.db.repositories.operation_exclusions import set_excluded_operations
from cryo.db.tenant_scope import get_scoped_resource
from cryo.table_config import service
from cryo.table_config.live import CatalogReadError
from cryo.table_config.models import (
    ConfigApplyRequest,
    ConfigApplyResponse,
    ConfigPreviewRequest,
    ConfigPreviewResponse,
    OperationExclusionsResponse,
    OperationExclusionsWrite,
    TableConfigResponse,
)
from cryo.table_config.operations import SPECS
from cryo.table_config.risk import max_risk

router = APIRouter(prefix="/api/v1", tags=["tables"])
ViewerPrincipalDep = Annotated[Principal, Depends(require_viewer)]
OperatorPrincipalDep = Annotated[Principal, Depends(require_operator)]


def _inputs(session: SessionDep, table_id: uuid.UUID) -> service.TableInputs:
    table = get_scoped_resource(session, Table, table_id, resource_type="Table")
    return service.load_inputs(session, table)


@router.get("/tables/{table_id}/config", response_model=TableConfigResponse)
def get_table_config(
    table_id: uuid.UUID,
    session: SessionDep,
    _principal: ViewerPrincipalDep,
    refresh: bool = False,
) -> TableConfigResponse:
    """Every curated setting with its live value, source, recommendation and change risk.

    Reads the Source catalog (metadata only, bounded). When the catalog is unreachable the
    response falls back to the last scan and ``can_edit`` is false.
    """
    inputs = _inputs(session, table_id)
    try:
        config = service.read_live(inputs, use_cache=not refresh)
    except CatalogReadError as exc:
        return service.build_response(inputs, None, catalog_error=str(exc))
    return service.build_response(inputs, config)


@router.post("/tables/{table_id}/config/preview", response_model=ConfigPreviewResponse)
def preview_table_config(
    table_id: uuid.UUID,
    payload: ConfigPreviewRequest,
    session: SessionDep,
    _principal: ViewerPrincipalDep,
) -> ConfigPreviewResponse:
    inputs = _inputs(session, table_id)
    try:
        config = service.read_live(inputs)
    except CatalogReadError as exc:
        raise HTTPException(status_code=503, detail=f"Catalog metadata read failed: {exc}") from exc
    previews = service.preview(payload.changes, service.build_context(inputs, config), inputs)
    pending = [p for p in previews if not p.no_op and not p.blocked]
    overall = max_risk([p.risk for p in pending])
    return ConfigPreviewResponse(
        changes=previews,
        overall_risk=overall,
        requires_confirmation=overall == "high",
        blocked=any(p.blocked for p in previews),
    )


@router.post("/tables/{table_id}/config/apply", response_model=ConfigApplyResponse)
def apply_table_config(
    table_id: uuid.UUID,
    payload: ConfigApplyRequest,
    session: SessionDep,
    principal: OperatorPrincipalDep,
) -> ConfigApplyResponse:
    """Apply a batch of setting changes. Properties go to the catalog in one metadata commit;
    Cryo retention settings become per-table preset overrides (admin only). Every change is
    audited, and high-risk batches need ``confirm`` plus a written ``reason``."""
    inputs = _inputs(session, table_id)
    try:
        outcome = service.apply_config(
            session,
            inputs,
            payload.changes,
            actor=principal.subject,
            is_admin=principal.can(Role.ADMIN),
            confirm=payload.confirm,
            reason=payload.reason,
        )
    except service.ConfigApplyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    refreshed_inputs = _inputs(session, table_id)
    count = len(outcome.applied)
    return ConfigApplyResponse(
        applied=outcome.applied,
        audit_log_ids=outcome.audit_ids,
        message=f"Applied {count} change{'s' if count != 1 else ''}.",
        config=service.build_response(
            refreshed_inputs, outcome.config, catalog_error=outcome.catalog_error
        ),
    )


@router.put("/tables/{table_id}/config/operations", response_model=OperationExclusionsResponse)
def put_operation_exclusions(
    table_id: uuid.UUID,
    payload: OperationExclusionsWrite,
    session: SessionDep,
    principal: OperatorPrincipalDep,
) -> OperationExclusionsResponse:
    """Replace the set of maintenance operations no run may plan for this table.

    Every plan path (scheduled, ad-hoc, governor admission, nightly batch) drops them.
    Excluding a high-risk operation needs ``confirm``; every change is audited.
    """
    inputs = _inputs(session, table_id)
    unknown = sorted({op for op in payload.excluded if op not in SPECS})
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"not a maintenance operation: {', '.join(unknown)}"
        )
    already = {row.operation for row in inputs.operation_exclusions}
    risky = sorted(op for op in set(payload.excluded) - already if SPECS[op].exclude_risk == "high")
    if risky and not payload.confirm:
        raise HTTPException(
            status_code=409,
            detail={
                "requires_confirmation": True,
                "operations": risky,
                "message": "Excluding these operations is high risk; resend with confirm.",
            },
        )
    change = set_excluded_operations(
        session,
        inputs.table.id,
        payload.excluded,
        reason=payload.reason.strip(),
        actor=principal.subject,
    )
    session.commit()
    refreshed = _inputs(session, table_id)
    try:
        config = service.read_live(refreshed)
    except CatalogReadError:
        config = None
    parts = []
    if change.added:
        parts.append(f"excluded {', '.join(SPECS[op].label for op in change.added)}")
    if change.removed:
        parts.append(f"re-enabled {', '.join(SPECS[op].label for op in change.removed)}")
    message = ("; ".join(parts) or "no change").capitalize() + "."
    return OperationExclusionsResponse(
        added=list(change.added),
        removed=list(change.removed),
        audit_log_ids=list(change.audit_ids),
        message=message,
        operations=service.operations_read(refreshed, service.build_context(refreshed, config)),
    )
