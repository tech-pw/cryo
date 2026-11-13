"""Shared S3 warehouse-path anomaly APIs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cryo.anomalies.report import (
    empty_report_payload,
    latest_shared_s3_report,
    notify_shared_s3_report,
    refresh_shared_s3_report,
    report_to_payload,
)
from cryo.api.deps import SessionDep, require_editor, require_viewer
from cryo.api.security import Principal

router = APIRouter(prefix="/api/v1/anomalies", tags=["anomalies"])


class SharedS3TableMemberRead(BaseModel):
    source_id: uuid.UUID
    source_name: str
    source_type: str
    schema_name: str
    table_name: str
    location: str
    table_id: uuid.UUID | None = None
    qualified_name: str


class SharedS3GroupRead(BaseModel):
    shared_path: str
    kind: Literal["exact", "nested"]
    table_count: int
    tables: list[SharedS3TableMemberRead]


class SharedS3SourceSummaryRead(BaseModel):
    source_id: uuid.UUID
    source_name: str
    source_type: str
    mode: Literal["live", "db_fallback"]
    table_count: int
    skipped_count: int
    error: str | None = None


class SharedS3ReportRead(BaseModel):
    id: uuid.UUID | None = None
    generated_at: datetime | None = None
    sources_harvested: int = 0
    table_count: int = 0
    overlapping_group_count: int = 0
    overlapping_table_count: int = 0
    skipped_count: int = 0
    sources: list[SharedS3SourceSummaryRead] = Field(default_factory=list)
    groups: list[SharedS3GroupRead] = Field(default_factory=list)
    slack_sent: bool | None = None
    slack_skipped_reason: str | None = None
    slack_targets: int | None = None


def _parse_report(payload: dict[str, Any]) -> SharedS3ReportRead:
    return SharedS3ReportRead.model_validate(payload)


@router.get("/shared-s3", response_model=SharedS3ReportRead)
def get_shared_s3_report(
    request: Request,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> Any:
    from cryo.api.conditional_cache import conditional_cache, shared_s3_report_stamps

    def _compute() -> SharedS3ReportRead:
        report = latest_shared_s3_report(session)
        if report is None:
            return _parse_report(empty_report_payload())
        return _parse_report(report_to_payload(report))

    return conditional_cache.serve(
        request=request,
        route="/api/v1/anomalies/shared-s3",
        principal=principal,
        params={},
        stamps=shared_s3_report_stamps(session),
        compute=_compute,
    )


@router.post("/shared-s3/refresh", response_model=SharedS3ReportRead)
def refresh_shared_s3(
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_editor)],
) -> SharedS3ReportRead:
    report = refresh_shared_s3_report(session)
    return _parse_report(report_to_payload(report))


@router.post("/shared-s3/notify", response_model=SharedS3ReportRead)
async def notify_shared_s3(
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_editor)],
) -> SharedS3ReportRead:
    try:
        payload = await notify_shared_s3_report(session)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _parse_report(payload)
