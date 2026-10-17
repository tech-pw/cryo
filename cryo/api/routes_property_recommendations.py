from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cryo.api.deps import SessionDep, require_editor, require_viewer
from cryo.api.security import Principal
from cryo.db.models import AuditLog, Table, TableStats
from cryo.scanner.property_recommendations import (
    apply_table_properties,
    compute_property_recommendations,
)

router = APIRouter(prefix="/api/v1", tags=["tables"])
ViewerPrincipalDep = Annotated[Principal, Depends(require_viewer)]
EditorPrincipalDep = Annotated[Principal, Depends(require_editor)]


class PropertyRecommendationRead(BaseModel):
    property_key: str
    current_value: str | None = None
    recommended_value: str | None = None
    reason: str
    tier: Literal["tier1", "tier2", "tier3"]
    action: Literal[
        "apply",
        "link_layout",
        "link_bloom",
        "link_run",
        "read_only",
        "banner",
        "informational",
    ]
    layout_recommendation_id: uuid.UUID | None = None
    bloom_filter_column: str | None = None
    needs_confirmation: bool = False


class PropertyRecommendationsResponse(BaseModel):
    table_id: uuid.UUID
    generated_at: str
    items: list[PropertyRecommendationRead] = Field(default_factory=list)
    gc_blocked: bool = False


class ApplyTablePropertiesRequest(BaseModel):
    properties: dict[str, str] = Field(min_length=1)
    confirm_risky: bool = False
    note: str | None = None


class ApplyTablePropertiesResponse(BaseModel):
    table_id: uuid.UUID
    applied: dict[str, str]
    audit_log_id: int
    message: str


@router.get(
    "/tables/{table_id}/property-recommendations",
    response_model=PropertyRecommendationsResponse,
)
def get_property_recommendations(
    table_id: uuid.UUID,
    session: SessionDep,
    _principal: ViewerPrincipalDep,
) -> PropertyRecommendationsResponse:
    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")

    items = compute_property_recommendations(session, table_id)
    stats = session.get(TableStats, table_id)
    raw = dict((stats.raw or {}) if stats else {})
    gc_blocked = raw.get("gc_enabled") is False

    from datetime import UTC, datetime

    return PropertyRecommendationsResponse(
        table_id=table_id,
        generated_at=datetime.now(UTC).isoformat(),
        gc_blocked=gc_blocked,
        items=[
            PropertyRecommendationRead(
                property_key=item.property_key,
                current_value=item.current_value,
                recommended_value=item.recommended_value,
                reason=item.reason,
                tier=item.tier,
                action=item.action,
                layout_recommendation_id=item.layout_recommendation_id,
                bloom_filter_column=item.bloom_filter_column,
                needs_confirmation=item.needs_confirmation,
            )
            for item in items
        ],
    )


@router.post(
    "/tables/{table_id}/properties/apply",
    response_model=ApplyTablePropertiesResponse,
)
def apply_table_properties_endpoint(
    table_id: uuid.UUID,
    payload: ApplyTablePropertiesRequest,
    session: SessionDep,
    principal: EditorPrincipalDep,
) -> ApplyTablePropertiesResponse:
    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")

    if "format-version" in payload.properties:
        raise HTTPException(
            status_code=400,
            detail=(
                "format-version is reserved and cannot be set as a property; upgrade it from "
                "the table's Config tab (POST /tables/{id}/config/apply)"
            ),
        )

    try:
        applied = apply_table_properties(
            session,
            table_id,
            payload.properties,
            confirm_risky=payload.confirm_risky,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Property apply failed: {exc}") from exc

    stats = session.get(TableStats, table_id)
    if stats is not None:
        raw: dict[str, Any] = dict(stats.raw or {})
        iceberg = dict(raw.get("iceberg_properties") or {})
        iceberg.update(applied)
        raw["iceberg_properties"] = iceberg
        if "write.target-file-size-bytes" in applied:
            try:
                stats.target_file_size_bytes = int(applied["write.target-file-size-bytes"])
            except (TypeError, ValueError):
                pass
        if "write.distribution-mode" in applied:
            stats.write_distribution_mode = applied["write.distribution-mode"]
        if "write.sort-order" in applied:
            stats.has_sort_order = True
        if "gc.enabled" in applied:
            raw["gc_enabled"] = applied["gc.enabled"].lower() == "true"
        stats.raw = raw

    detail_parts = [f"{k}={v}" for k, v in sorted(applied.items())]
    if payload.note:
        detail_parts.append(f"note={payload.note}")
    audit = AuditLog(
        table_id=table_id,
        action="apply_table_properties",
        status="OK",
        engine="pyiceberg",
        detail=" ".join(detail_parts),
        actor=principal.subject,
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)

    return ApplyTablePropertiesResponse(
        table_id=table_id,
        applied=applied,
        audit_log_id=int(audit.id),
        message=f"Applied {len(applied)} table property(ies).",
    )
