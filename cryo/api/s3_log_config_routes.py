"""API routes for S3 request-cost tracking config (``docs/s3-request-cost-tracking.md`` §8).

Endpoints (all under ``/api/v1``):

- ``GET    /sources/{id}/s3-log-configs``                       — list discovered buckets + status
- ``PATCH  /sources/{id}/s3-log-configs/{bucket}``               — manual target/creds/enable
- ``POST   /sources/{id}/s3-log-configs/{bucket}/check-logging`` — re-run GetBucketLogging on demand
- ``POST   /sources/{id}/s3-log-configs/{bucket}/probe``         — validate a manually-typed target
- ``POST   /sources/{id}/s3-log-configs/{bucket}/ingest``        — manually trigger an ingestion run
- ``POST   /sources/{id}/s3-log-configs/{bucket}/provision``     — one-click auto-provision (§4.3)
- ``POST   /sources/{id}/s3-log-configs/{bucket}/teardown``      — destructive cleanup (§9.4)
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from cryo.api.deps import SessionDep, require_admin, require_operator, require_viewer
from cryo.api.schemas import (
    S3LogConfigCheckResponse,
    S3LogConfigPatch,
    S3LogConfigProbeResponse,
    S3LogConfigRead,
    S3LogIngestResponse,
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
