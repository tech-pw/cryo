"""API routes for S3 request-cost tracking config (``docs/s3-request-cost-tracking.md`` §8).

Endpoints (all under ``/api/v1``):

- ``GET    /sources/{id}/s3-log-configs``                       — list discovered buckets + status
- ``PATCH  /sources/{id}/s3-log-configs/{bucket}``               — manual target/creds/enable
- ``POST   /sources/{id}/s3-log-configs/{bucket}/check-logging`` — re-run GetBucketLogging on demand
- ``POST   /sources/{id}/s3-log-configs/{bucket}/probe``         — validate a manually-typed target
- ``POST   /sources/{id}/s3-log-configs/{bucket}/ingest``        — manually trigger an ingestion run
- ``POST   /sources/{id}/s3-log-configs/{bucket}/provision``     — one-click auto-provision (§4.3)
- ``POST   /sources/{id}/s3-log-configs/{bucket}/teardown``      — destructive cleanup (§9.4)
- ``GET    /sources/{id}/s3-request-cost-rollup``                — fleet-view rollup across all
  of a source's tracked buckets (Cost page "S3 requests" tab)
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from cryo.api.deps import SessionDep, require_admin, require_operator, require_viewer
from cryo.api.schemas import (
    S3LogConfigCheckResponse,
    S3LogConfigPatch,
    S3LogConfigProbeResponse,
    S3LogConfigRead,
    S3LogIngestResponse,
    S3LogProvisionRequest,
    S3LogProvisionResponse,
    S3LogTeardownRequest,
    S3LogTeardownResponse,
    S3RequestCostByDayRead,
    S3RequestCostByTierRead,
    S3RequestCostRollupRead,
)
from cryo.db.models import S3BucketLogConfig, Source
from cryo.db.tenant_scope import get_scoped_resource
from cryo.integrations import s3_bucket_logging
from cryo.security.crypto import encrypt_secret

router = APIRouter(prefix="/api/v1", tags=["s3-request-cost"])


def _to_read(config: S3BucketLogConfig) -> S3LogConfigRead:
    return S3LogConfigRead(
        id=config.id,
        source_id=config.source_id,
        bucket=config.bucket,
        region=config.region,
        access_key_id=config.access_key_id,
        logging_status=config.logging_status,
        log_target_bucket=config.log_target_bucket,
        log_target_prefix=config.log_target_prefix,
        manual_override=config.manual_override,
        enabled=config.enabled,
        last_checked_at=config.last_checked_at,
        last_ingested_at=config.last_ingested_at,
        last_ingested_date=config.last_ingested_date,
        last_error=config.last_error,
        last_error_at=config.last_error_at,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def _get_config(session: SessionDep, source_id: UUID, bucket: str) -> S3BucketLogConfig:
    config = session.scalar(
        select(S3BucketLogConfig).where(
            S3BucketLogConfig.source_id == source_id, S3BucketLogConfig.bucket == bucket
        )
    )
    if config is None:
        raise HTTPException(status_code=404, detail=f"No S3 log config for bucket '{bucket}'")
    return config


@router.get(
    "/sources/{source_id}/s3-log-configs",
    response_model=list[S3LogConfigRead],
    dependencies=[Depends(require_viewer)],
)
def list_s3_log_configs(source_id: UUID, session: SessionDep) -> list[S3LogConfigRead]:
    """§4.1 — discover buckets from scanned table locations, then return every tracked bucket."""
    get_scoped_resource(session, Source, source_id, resource_type="Source")
    configs = s3_bucket_logging.discover_buckets_for_source(session, source_id)
    session.commit()
    return [_to_read(config) for config in sorted(configs, key=lambda c: c.bucket)]


@router.patch(
    "/sources/{source_id}/s3-log-configs/{bucket}",
    response_model=S3LogConfigRead,
    dependencies=[Depends(require_admin)],
)
def patch_s3_log_config(
    source_id: UUID, bucket: str, payload: S3LogConfigPatch, session: SessionDep
) -> S3LogConfigRead:
    config = _get_config(session, source_id, bucket)
    values = payload.model_dump(exclude_unset=True)
    if "secret_access_key" in values:
        secret = values.pop("secret_access_key")
        config.secret_ref = encrypt_secret(secret) if secret else None
    if values.get("log_target_bucket") is not None or values.get("log_target_prefix") is not None:
        values.setdefault("manual_override", True)
    for key, value in values.items():
        setattr(config, key, value)
    session.commit()
    session.refresh(config)
    return _to_read(config)


@router.post(
    "/sources/{source_id}/s3-log-configs/{bucket}/check-logging",
    response_model=S3LogConfigCheckResponse,
    dependencies=[Depends(require_operator)],
)
def check_logging(source_id: UUID, bucket: str, session: SessionDep) -> S3LogConfigCheckResponse:
    config = _get_config(session, source_id, bucket)
    s3_bucket_logging.check_bucket_logging(config)
    session.commit()
    return S3LogConfigCheckResponse(
        logging_status=config.logging_status,
        log_target_bucket=config.log_target_bucket,
        log_target_prefix=config.log_target_prefix,
        error=config.last_error,
    )


@router.post(
    "/sources/{source_id}/s3-log-configs/{bucket}/probe",
    response_model=S3LogConfigProbeResponse,
    dependencies=[Depends(require_operator)],
)
def probe_manual_target(
    source_id: UUID, bucket: str, session: SessionDep
) -> S3LogConfigProbeResponse:
    """§4.4 — validate a manually-typed log target without accepting it blind."""
    config = _get_config(session, source_id, bucket)
    result = s3_bucket_logging.probe_manual_target(config)
    return S3LogConfigProbeResponse(
        ok=result.ok, files_found=result.files_found, truncated=result.truncated, error=result.error
    )


@router.post(
    "/sources/{source_id}/s3-log-configs/{bucket}/ingest",
    response_model=S3LogIngestResponse,
    dependencies=[Depends(require_operator)],
)
def ingest_bucket(source_id: UUID, bucket: str, session: SessionDep) -> S3LogIngestResponse:
    from cryo.scanner import s3_request_cost_ingest

    config = _get_config(session, source_id, bucket)
    if not config.enabled:
        raise HTTPException(status_code=400, detail="This bucket's ingestion is not enabled")
    result = s3_request_cost_ingest.ingest_bucket(session, config)
    session.commit()
    return S3LogIngestResponse(
        bucket=bucket,
        rows_ingested=result.rows_ingested,
        tables_matched=result.tables_matched,
        unmatched_requests=result.unmatched_requests,
        through_date=result.through_date,
        error=result.error,
    )


@router.post(
    "/sources/{source_id}/s3-log-configs/{bucket}/provision",
    response_model=S3LogProvisionResponse,
    dependencies=[Depends(require_admin)],
)
def provision_logging(
    source_id: UUID, bucket: str, payload: S3LogProvisionRequest, session: SessionDep
) -> S3LogProvisionResponse:
    """§4.3 — one-click auto-provision. Writes to the user's AWS account; requires admin and
    is only ever called from an explicit, confirm-then-act frontend action."""
    from cryo.integrations import s3_bucket_provisioning

    config = _get_config(session, source_id, bucket)
    result = s3_bucket_provisioning.provision_logging(
        session, config, target_bucket=payload.target_bucket
    )
    if result.ok:
        session.commit()
    return S3LogProvisionResponse(
        ok=result.ok,
        log_target_bucket=result.log_target_bucket,
        log_target_prefix=result.log_target_prefix,
        error=result.error,
    )


@router.post(
    "/sources/{source_id}/s3-log-configs/{bucket}/teardown",
    response_model=S3LogTeardownResponse,
    dependencies=[Depends(require_admin)],
)
def teardown_bucket(
    source_id: UUID, bucket: str, payload: S3LogTeardownRequest, session: SessionDep
) -> S3LogTeardownResponse:
    """§9.4 — destructive cleanup, distinct from PATCH {enabled: false}. Removes the config row
    and, only for policy Cryo itself added, offers to revert it."""
    from cryo.integrations import s3_bucket_provisioning

    config = _get_config(session, source_id, bucket)
    result = s3_bucket_provisioning.teardown_bucket(
        session, config, revert_policy=payload.revert_policy
    )
    session.delete(config)
    session.commit()
    return S3LogTeardownResponse(
        ok=result.ok,
        policy_reverted=result.policy_reverted,
        warnings=result.warnings,
        error=result.error,
    )


@router.get(
    "/sources/{source_id}/s3-request-cost-rollup",
    response_model=S3RequestCostRollupRead,
    dependencies=[Depends(require_viewer)],
)
def get_source_s3_request_cost_rollup(
    source_id: UUID,
    session: SessionDep,
    lookback_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> S3RequestCostRollupRead:
    """Fleet-view rollup for the Cost page's "S3 requests" tab — aggregates every bucket this
    source tracks. 404 when the source has no S3BucketLogConfig rows at all (nothing
    discovered/configured yet), distinct from "configured but zero requests measured" which
    returns real zeros."""
    from cryo.scanner.s3_request_cost_ingest import compute_source_request_cost_rollup

    source = get_scoped_resource(session, Source, source_id, resource_type="Source")
    result = compute_source_request_cost_rollup(session, source_id, lookback_days=lookback_days)
    if result is None:
        raise HTTPException(
            status_code=404, detail="No S3 request-cost tracking configured for this source"
        )
    return S3RequestCostRollupRead(
        bucket=result.bucket,
        source_id=source_id,
        source_name=source.name,
        region=result.region,
        enabled=result.enabled,
        logging_status=result.logging_status,
        last_ingested_at=result.last_ingested_at,
        total_request_count_30d=result.total_request_count_30d,
        total_cost_usd_month=result.total_cost_usd_month,
        by_tier=S3RequestCostByTierRead(
            tier1_count=result.by_tier.tier1_count,
            tier2_count=result.by_tier.tier2_count,
            tier1_cost_usd=result.by_tier.tier1_cost_usd,
            tier2_cost_usd=result.by_tier.tier2_cost_usd,
        ),
        by_day=[
            S3RequestCostByDayRead(day=d.day, count=d.count, cost_usd=d.cost_usd)
            for d in result.by_day
        ],
        approximate=result.approximate,
    )
