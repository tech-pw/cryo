"""API routes for the CUR + S3 Inventory cost-attribution path.

Endpoints (all under ``/api/v1``):

- ``GET    /sources/{id}/cost-insight-config``             — read config or 404
- ``POST   /sources/{id}/cost-insight-config``             — create (idempotent per source)
- ``PATCH  /sources/{id}/cost-insight-config``             — partial update
- ``DELETE /sources/{id}/cost-insight-config``             — remove
- ``POST   /sources/{id}/cost-insight/ingest-cur``         — trigger CUR ingest from S3 / fixture
- ``POST   /sources/{id}/cost-insight/ingest-inventory``   — trigger S3 Inventory ingest
- ``GET    /sources/{id}/cost-insight/by-bucket``          — aggregated per-bucket totals
- ``GET    /sources/{id}/cost-insight/daily``              — daily time series
- ``GET    /sources/{id}/cost-insight/by-prefix``          — per-table-prefix size from Inventory
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from cryo.api.deps import SessionDep, require_admin, require_operator, require_viewer
from cryo.api.schemas import (
    AWSCostIngestResponse,
    AWSCostInsightConfigCreate,
    AWSCostInsightConfigPatch,
    AWSCostInsightConfigRead,
    CostByBucketItem,
    CostByBucketResponse,
    CostDailySeriesItem,
    CostDailySeriesResponse,
    SizeByPrefixItem,
    SizeByPrefixResponse,
)
from cryo.db.models import AWSCostInsightConfig, Source
from cryo.db.tenant_scope import get_scoped_resource
from cryo.integrations import aws_cost_insights
from cryo.security.crypto import encrypt_secret

router = APIRouter(prefix="/api/v1", tags=["cost-insights"])


def _to_read(config: AWSCostInsightConfig) -> AWSCostInsightConfigRead:
    return AWSCostInsightConfigRead(
        id=config.id,
        source_id=config.source_id,
        cur_bucket=config.cur_bucket,
        cur_prefix=config.cur_prefix,
        cur_report_name=config.cur_report_name,
        fixture_path=config.fixture_path,
        inventory_bucket=config.inventory_bucket,
        inventory_prefix=config.inventory_prefix,
        inventory_format=config.inventory_format or "CSV",
        access_key_id=config.access_key_id,
        enabled=config.enabled,
        last_ingested_at=config.last_ingested_at,
        last_ingested_through=config.last_ingested_through,
        last_ingest_ok=config.last_ingest_ok,
        last_ingest_error=config.last_ingest_error,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.get(
    "/sources/{source_id}/cost-insight-config",
    response_model=AWSCostInsightConfigRead,
    dependencies=[Depends(require_viewer)],
)
def get_cost_insight_config(source_id: UUID, session: SessionDep) -> AWSCostInsightConfigRead:
    config = aws_cost_insights.find_config_for_source(session, source_id)
    if config is None:
        raise HTTPException(status_code=404, detail="No AWS cost insight config for this source")
    return _to_read(config)


@router.post(
    "/sources/{source_id}/cost-insight-config",
    response_model=AWSCostInsightConfigRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_cost_insight_config(
    source_id: UUID, payload: AWSCostInsightConfigCreate, session: SessionDep
) -> AWSCostInsightConfigRead:
    get_scoped_resource(session, Source, source_id, resource_type="Source")
    existing = aws_cost_insights.find_config_for_source(session, source_id)
    if existing is not None:
        raise HTTPException(
            status_code=400, detail="AWS cost insight config already exists for this source"
        )
    config = AWSCostInsightConfig(
        source_id=source_id,
        cur_bucket=payload.cur_bucket,
        cur_prefix=payload.cur_prefix,
        cur_report_name=payload.cur_report_name,
        fixture_path=payload.fixture_path,
        inventory_bucket=payload.inventory_bucket,
        inventory_prefix=payload.inventory_prefix,
        inventory_format=payload.inventory_format,
        access_key_id=payload.access_key_id,
        secret_ref=encrypt_secret(payload.secret_access_key) if payload.secret_access_key else None,
        enabled=payload.enabled,
    )
    session.add(config)
    session.commit()
    session.refresh(config)
    return _to_read(config)


@router.patch(
    "/sources/{source_id}/cost-insight-config",
    response_model=AWSCostInsightConfigRead,
    dependencies=[Depends(require_admin)],
)
def patch_cost_insight_config(
    source_id: UUID, payload: AWSCostInsightConfigPatch, session: SessionDep
) -> AWSCostInsightConfigRead:
    config = aws_cost_insights.find_config_for_source(session, source_id)
    if config is None:
        raise HTTPException(status_code=404, detail="No AWS cost insight config for this source")
    values = payload.model_dump(exclude_unset=True)
    if "secret_access_key" in values:
        secret = values.pop("secret_access_key")
        config.secret_ref = encrypt_secret(secret) if secret else None
    for key, value in values.items():
        setattr(config, key, value)
    session.commit()
    session.refresh(config)
    return _to_read(config)


@router.delete(
    "/sources/{source_id}/cost-insight-config",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_cost_insight_config(source_id: UUID, session: SessionDep) -> None:
    config = aws_cost_insights.find_config_for_source(session, source_id)
    if config is None:
        raise HTTPException(status_code=404, detail="No AWS cost insight config for this source")
    session.delete(config)
    session.commit()


@router.post(
    "/sources/{source_id}/cost-insight/ingest-cur",
    response_model=AWSCostIngestResponse,
    dependencies=[Depends(require_operator)],
)
def ingest_cur(source_id: UUID, session: SessionDep) -> AWSCostIngestResponse:
    """Trigger a CUR ingest from S3, or from a local fixture when one is configured.

    When ``fixture_path`` is set on the config (typical for dev/CI), reads that file instead
    of listing the configured CUR bucket — invaluable for testing without real AWS access.
    """
    config = aws_cost_insights.find_config_for_source(session, source_id)
    if config is None:
        raise HTTPException(status_code=404, detail="No AWS cost insight config for this source")

    if config.fixture_path:
        summary = aws_cost_insights.ingest_cur_from_fixture(session, config, config.fixture_path)
    else:
        summary = aws_cost_insights.ingest_cur_for_source(session, config)

    config.last_ingested_at = datetime.now(UTC)
    if summary.error is None:
        config.last_ingest_ok = True
        config.last_ingest_error = None
        if summary.through_date is not None:
            config.last_ingested_through = datetime.combine(
                summary.through_date, datetime.min.time(), tzinfo=UTC
            )
    else:
        config.last_ingest_ok = False
        config.last_ingest_error = summary.error
    session.commit()

    return AWSCostIngestResponse(
        rows_ingested=summary.rows_ingested,
        files_processed=summary.files_processed,
        through_date=summary.through_date,
        error=summary.error,
    )


@router.post(
    "/sources/{source_id}/cost-insight/ingest-inventory",
    response_model=AWSCostIngestResponse,
    dependencies=[Depends(require_operator)],
)
def ingest_inventory(source_id: UUID, session: SessionDep) -> AWSCostIngestResponse:
    config = aws_cost_insights.find_config_for_source(session, source_id)
    if config is None:
        raise HTTPException(status_code=404, detail="No AWS cost insight config for this source")
    summary = aws_cost_insights.ingest_inventory_for_source(session, config)
    config.last_ingested_at = datetime.now(UTC)
    if summary.error is None:
        config.last_ingest_ok = True
        config.last_ingest_error = None
    else:
        config.last_ingest_ok = False
        config.last_ingest_error = summary.error
    session.commit()
    return AWSCostIngestResponse(
        rows_ingested=summary.rows_ingested,
        files_processed=summary.files_processed,
        inventory_date=summary.inventory_date,
        error=summary.error,
    )


@router.get(
    "/sources/{source_id}/cost-insight/by-bucket",
    response_model=CostByBucketResponse,
    dependencies=[Depends(require_viewer)],
)
def cost_by_bucket(
    source_id: UUID,
    session: SessionDep,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> CostByBucketResponse:
    rows = aws_cost_insights.cost_by_bucket(session, source_id, start=start, end=end, limit=limit)
    total = sum(item["total_cost_usd"] for item in rows)
    return CostByBucketResponse(
        source_id=source_id,
        start=start,
        end=end,
        total_cost_usd=total,
        items=[CostByBucketItem(**item) for item in rows],
    )


@router.get(
    "/sources/{source_id}/cost-insight/daily",
    response_model=CostDailySeriesResponse,
    dependencies=[Depends(require_viewer)],
)
def cost_daily(
    source_id: UUID,
    session: SessionDep,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> CostDailySeriesResponse:
    rows = aws_cost_insights.cost_daily_series(session, source_id, start=start, end=end)
    return CostDailySeriesResponse(
        source_id=source_id,
        items=[CostDailySeriesItem(**item) for item in rows],
    )


@router.get(
    "/sources/{source_id}/cost-insight/by-prefix",
    response_model=SizeByPrefixResponse,
    dependencies=[Depends(require_viewer)],
)
def size_by_prefix(
    source_id: UUID,
    session: SessionDep,
    inventory_date: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SizeByPrefixResponse:
    rows = aws_cost_insights.size_by_table_prefix(
        session, source_id, inventory_date=inventory_date, limit=limit
    )
    return SizeByPrefixResponse(
        source_id=source_id,
        inventory_date=inventory_date,
        items=[SizeByPrefixItem(**item) for item in rows],
    )
