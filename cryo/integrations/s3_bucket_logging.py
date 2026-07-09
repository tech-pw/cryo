"""S3 server access-log discovery, logging-status checks, and manual-target validation.

Implements §4.1/§4.2/§4.4 of ``docs/s3-request-cost-tracking.md``. §4.3's one-click
auto-provision (``PutBucketLogging`` + bucket-policy merge) and §9.4's teardown live in
``cryo/integrations/s3_bucket_provisioning.py`` — kept in a separate module since that one
writes to the user's AWS account and this one only ever reads.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AWSIntegration, S3BucketLogConfig, Table
from cryo.integrations.aws_s3_metrics import AWSCredentials
from cryo.scanner.orphans import parse_s3_location
from cryo.security.crypto import decrypt_secret

logger = logging.getLogger(__name__)

# botocore's default connection pool (10) is smaller than this codebase's own concurrent S3
# reader (cryo/scanner/s3_request_cost_ingest.py's default 16-thread ingest) -- with more
# concurrent callers than pool slots, boto3 discards and reopens connections on every request
# past the limit instead of reusing them (visible as "Connection pool is full, discarding
# connection" warnings), turning TCP/TLS handshake overhead into the bottleneck. Sized with
# headroom above the current default concurrency; single-threaded callers are unaffected.
_S3_CLIENT_CONFIG = Config(max_pool_connections=20)


def credentials_for_config(config: S3BucketLogConfig) -> AWSCredentials | None:
    """Resolve a bucket's own credentials.

    Returns ``None`` if this row has never been given credentials — the caller falls through
    to manual entry (§4.4) with no auto-detect, per the "never implicitly borrow" rule in the
    ``S3BucketLogConfig`` docstring (``cryo/db/models.py``).
    """
    if not config.access_key_id or not config.secret_ref:
        return None
    secret = decrypt_secret(config.secret_ref)
    if secret is None:
        return None
    return AWSCredentials(
        region=config.region or "us-east-1",
        access_key_id=config.access_key_id,
        secret_access_key=secret,
    )


def _session(creds: AWSCredentials | None) -> Any:
    if creds is None:
        return boto3.Session()
    kwargs: dict[str, Any] = {"region_name": creds.region}
    if creds.access_key_id and creds.secret_access_key:
        kwargs["aws_access_key_id"] = creds.access_key_id
        kwargs["aws_secret_access_key"] = creds.secret_access_key
    return boto3.Session(**kwargs)


def boto_session_for_config(config: S3BucketLogConfig) -> Any | None:
    """Build a boto3 Session from a bucket's own credentials, or ``None`` if it has none."""
    creds = credentials_for_config(config)
    return _session(creds) if creds is not None else None


def s3_client_for_config(config: S3BucketLogConfig) -> Any | None:
    """Build a boto3 S3 client from a bucket's own credentials, or ``None`` if it has none."""
    session = boto_session_for_config(config)
    return session.client("s3", config=_S3_CLIENT_CONFIG) if session is not None else None


def discover_buckets_for_source(session: Session, source_id: uuid.UUID) -> list[S3BucketLogConfig]:
    """§4.1 — auto-discover every bucket this source's tables actually live in.

    Pure DB read plus idempotent upsert, no AWS call: ``SELECT DISTINCT bucket FROM tables``
    (via ``Table.location``, populated in ``persist_table_analysis``), then a config row is
    created for any bucket not already tracked. Self-maintaining — re-running after new tables
    are scanned or moved just adds any newly-seen buckets, never duplicates existing rows.

    ``Table.location`` is the only signal discovery reads. For sources added after the
    migration that haven't been scanned yet, the user must trigger a scan to populate
    it -- documented in the panel's empty-state copy. A previous revision tried to
    enumerate live tables from the catalog as a fallback, but that's O(tables) round-trips
    on every panel refresh and hangs the request thread for sources with slow catalogs
    or many tables. The fallback was reverted; the right place for that is a
    backgrounded "backfill from catalog" worker, not a synchronous request path.
    """
    locations = session.scalars(
        select(Table.location).where(Table.source_id == source_id, Table.location.is_not(None))
    ).all()
    buckets: set[str] = set()
    for location in locations:
        if location is None:
            continue
        bucket, _prefix = parse_s3_location(location)
        if bucket:
            buckets.add(bucket)

    existing = {
        row.bucket: row
        for row in session.scalars(
            select(S3BucketLogConfig).where(S3BucketLogConfig.source_id == source_id)
        )
    }

    # A brand-new bucket's config row starts with no credentials of its own (per the "never
    # implicitly borrow" rule) -- except that requiring every adopter to re-type AWS keys they
    # already gave Cryo for this same source, once per bucket, directly fights the "minimal
    # config" goal (doc §2). Copying the source's existing AWSIntegration credentials *once, at
    # creation time* is a one-time explicit seed, not a runtime borrow -- the row then owns that
    # value and can be repointed independently (e.g. to a narrower role) without affecting
    # AWSIntegration or any other bucket's config.
    seed_integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )

    created: list[S3BucketLogConfig] = []
    for bucket in sorted(buckets):
        if bucket in existing:
            continue
        config = S3BucketLogConfig(
            source_id=source_id,
            bucket=bucket,
            region=seed_integration.region if seed_integration else None,
            access_key_id=seed_integration.access_key_id if seed_integration else None,
            secret_ref=seed_integration.secret_ref if seed_integration else None,
        )
        session.add(config)
        created.append(config)

    if created:
        session.flush()
    return list(existing.values()) + created


def check_bucket_logging(config: S3BucketLogConfig) -> None:
    """§4.2 — call ``GetBucketLogging`` and update ``config`` in place.

    Never raises: every failure mode (no credentials, ``AccessDenied``, any other AWS error)
    lands in ``logging_status='unknown'`` with a human-readable ``last_error``, which is exactly
    the "go straight to manual entry, with the exact missing permission named" case in §4.2
    step 4. Caller is responsible for committing.
    """
    config.last_checked_at = datetime.now(UTC)
    creds = credentials_for_config(config)
    if creds is None:
        config.logging_status = "unknown"
        config.last_error = "No AWS credentials configured for this bucket yet"
        config.last_error_at = config.last_checked_at
        return

    try:
        client = _session(creds).client("s3")
        response = client.get_bucket_logging(Bucket=config.bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code == "AccessDenied":
            config.last_error = (
                "Access denied calling s3:GetBucketLogging -- grant that permission on "
                f"'{config.bucket}' and retry, or enter the log target manually"
            )
        else:
            config.last_error = f"{code}: {exc.response.get('Error', {}).get('Message', str(exc))}"
        config.logging_status = "unknown"
        config.last_error_at = config.last_checked_at
        return
    except Exception as exc:  # pragma: no cover - defensive: never crash a status check
        logger.warning("Unexpected error checking GetBucketLogging for %s: %s", config.bucket, exc)
        config.logging_status = "unknown"
        config.last_error = str(exc)
        config.last_error_at = config.last_checked_at
        return

    logging_enabled = response.get("LoggingEnabled")
    config.last_error = None
    config.last_error_at = None
    if logging_enabled:
        config.logging_status = "enabled"
        if not config.manual_override:
            config.log_target_bucket = logging_enabled.get("TargetBucket")
            config.log_target_prefix = logging_enabled.get("TargetPrefix")
    else:
        config.logging_status = "disabled"


@dataclass(frozen=True)
class ManualProbeResult:
    ok: bool
    files_found: int
    truncated: bool
    error: str | None = None


def probe_manual_target(config: S3BucketLogConfig) -> ManualProbeResult:
    """§4.4 — lightweight validation for a manually-typed log target.

    A single ``ListObjectsV2`` page (up to 1,000 keys) against the given target/prefix -- enough
    to tell the user "found N log files" or "nothing here yet" without a full listing scan.
    """
    creds = credentials_for_config(config)
    if not config.log_target_bucket:
        return ManualProbeResult(
            ok=False, files_found=0, truncated=False, error="No target bucket set"
        )
    try:
        client = _session(creds).client("s3")
        response = client.list_objects_v2(
            Bucket=config.log_target_bucket,
            Prefix=config.log_target_prefix or "",
            MaxKeys=1000,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        return ManualProbeResult(ok=False, files_found=0, truncated=False, error=code)
    except Exception as exc:  # pragma: no cover - defensive
        return ManualProbeResult(ok=False, files_found=0, truncated=False, error=str(exc))

    count = int(response.get("KeyCount", 0) or 0)
    return ManualProbeResult(
        ok=True, files_found=count, truncated=bool(response.get("IsTruncated"))
    )
