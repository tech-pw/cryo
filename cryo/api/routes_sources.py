"""Source-domain routes for AWS integration.

First per-domain extraction out of the monolithic ``cryo/api/routes.py``,
per Move 22 in ``docs/strategy-next-moves-2026-07.md`` and the decomposition
plan in ``docs/RATING_2026.md`` §10. Keeps the AWS integration endpoints in
their own file (under 200 LoC) so the main ``routes.py`` can be split
further along other natural seams (sources, destinations, presets,
tables, maintenance, runs) without each step being a single unwieldy diff.

Registering the router is done in ``cryo/api/app.py`` next to the other
``include_router`` calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from cryo.api.deps import (
    SessionDep,
    require_admin,
    require_operator,
    require_viewer,
)
from cryo.api.schemas import (
    AWSConnectionTestResponse,
    AWSIntegrationCreate,
    AWSIntegrationPatch,
    AWSIntegrationRead,
    AWSUsageResponse,
)
from cryo.db.models import AWSIntegration, Source
from cryo.db.tenant_scope import get_scoped_resource
from cryo.integrations.aws_credentials import credentials_from_integration
from cryo.integrations.aws_s3_metrics import (
    fetch_bucket_storage_metrics,
    fetch_s3_price_per_gb,
)
from cryo.integrations.aws_s3_metrics import (
    test_aws_connection as check_aws_connection,
)
from cryo.security.crypto import encrypt_secret

router = APIRouter(prefix="/api/v1", tags=["sources"])


@router.get(
    "/sources/{source_id}/aws-integration",
    response_model=AWSIntegrationRead,
    dependencies=[Depends(require_viewer)],
    tags=["sources"],
)
def get_aws_integration(source_id: uuid.UUID, session: SessionDep) -> AWSIntegration:
    integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="No AWS integration configured")
    return integration


@router.post(
    "/sources/{source_id}/aws-integration",
    response_model=AWSIntegrationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    tags=["sources"],
)
def create_aws_integration(
    source_id: uuid.UUID, payload: AWSIntegrationCreate, session: SessionDep
) -> AWSIntegration:
    get_scoped_resource(session, Source, source_id, resource_type="Source")
    existing = session.scalar(select(AWSIntegration).where(AWSIntegration.source_id == source_id))
    if existing is not None:
        raise HTTPException(
            status_code=400, detail="AWS integration already exists for this source"
        )
    integration = AWSIntegration(
        source_id=source_id,
        bucket=payload.bucket,
        region=payload.region,
        access_key_id=payload.access_key_id,
        secret_ref=encrypt_secret(payload.secret_access_key),
        enabled=payload.enabled,
    )
    session.add(integration)
    session.commit()
    session.refresh(integration)
    return integration


@router.patch(
    "/sources/{source_id}/aws-integration",
    response_model=AWSIntegrationRead,
    dependencies=[Depends(require_admin)],
    tags=["sources"],
)
def patch_aws_integration(
    source_id: uuid.UUID, payload: AWSIntegrationPatch, session: SessionDep
) -> AWSIntegration:
    integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="No AWS integration configured")
    values = payload.model_dump(exclude_unset=True)
    if "secret_access_key" in values:
        values["secret_ref"] = encrypt_secret(values.pop("secret_access_key"))
    from cryo.api.routes import _apply_patch

    _apply_patch(integration, values)
    session.commit()
    session.refresh(integration)
    return integration


@router.delete(
    "/sources/{source_id}/aws-integration",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    tags=["sources"],
)
def delete_aws_integration(source_id: uuid.UUID, session: SessionDep) -> None:
    integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="No AWS integration configured")
    session.delete(integration)
    session.commit()


@router.post(
    "/sources/{source_id}/aws-integration/test",
    response_model=AWSConnectionTestResponse,
    dependencies=[Depends(require_operator)],
    tags=["sources"],
)
def test_aws_integration(source_id: uuid.UUID, session: SessionDep) -> AWSConnectionTestResponse:
    """Verify AWS credentials work via sts:GetCallerIdentity — free, no AWS charges."""
    integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="No AWS integration configured")
    result = check_aws_connection(credentials_from_integration(integration))
    integration.last_tested_at = datetime.now(UTC)
    integration.last_test_ok = result.ok
    session.commit()
    return AWSConnectionTestResponse(
        ok=result.ok, account_id=result.account_id, arn=result.arn, error=result.error
    )


@router.get(
    "/sources/{source_id}/aws-usage",
    response_model=AWSUsageResponse,
    dependencies=[Depends(require_viewer)],
    tags=["sources"],
)
def get_aws_usage(source_id: uuid.UUID, session: SessionDep) -> AWSUsageResponse:
    """Real S3 bucket size/object count (free daily CloudWatch metrics) and the real regional
    $/GB-month rate (free Price List API) — no data scan, no AWS charges."""
    integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )
    if integration is None or not integration.enabled:
        raise HTTPException(status_code=404, detail="No enabled AWS integration configured")
    credentials = credentials_from_integration(integration)
    metrics = fetch_bucket_storage_metrics(integration.bucket, credentials)
    price = fetch_s3_price_per_gb(integration.region, credentials)
    return AWSUsageResponse(
        bucket=metrics.bucket,
        region=metrics.region,
        measured_at=metrics.measured_at,
        size_bytes_by_storage_class=dict(metrics.size_bytes_by_storage_class),
        total_size_bytes=metrics.total_size_bytes,
        object_count=metrics.object_count,
        price_per_gb_month=price.price_per_gb_month if price else None,
        price_source=price.source if price else None,
    )
