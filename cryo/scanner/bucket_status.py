"""Does deleting files in this bucket lower the bill? (#538)

On a versioned bucket a delete only adds a delete marker; the old version keeps costing
until a lifecycle rule expires noncurrent versions. Two cheap calls per bucket answer it,
GetBucketVersioning and GetBucketLifecycleConfiguration, cached in ``s3_bucket_status``
for ``s3_bucket_status_refresh_hours``. Missing permissions record ``unknown``; the check
never fails a scan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import S3BucketStatus

logger = logging.getLogger(__name__)

_DENIED = frozenset({"AccessDenied", "AllAccessDisabled", "UnauthorizedOperation"})


@dataclass(frozen=True)
class BucketStatus:
    versioning: str | None
    noncurrent_expiration: bool | None
    error: str | None = None


def check_bucket(client: Any, bucket: str) -> BucketStatus:
    """``versioning`` is Enabled / Suspended / Disabled; None for either field is unknown."""
    errors: list[str] = []
    versioning: str | None
    try:
        versioning = str(client.get_bucket_versioning(Bucket=bucket).get("Status") or "Disabled")
    except Exception as exc:
        versioning = None
        errors.append(f"GetBucketVersioning: {_error_code(exc)}")
    noncurrent: bool | None
    try:
        rules = client.get_bucket_lifecycle_configuration(Bucket=bucket).get("Rules") or []
        noncurrent = any(_expires_noncurrent(rule) for rule in rules)
    except Exception as exc:
        code = _error_code(exc)
        if code == "NoSuchLifecycleConfiguration":
            noncurrent = False
        else:
            noncurrent = None
            errors.append(f"GetBucketLifecycleConfiguration: {code}")
    return BucketStatus(versioning, noncurrent, "; ".join(errors) or None)


def refresh_bucket_status(
    session: Session,
    client: Any,
    bucket: str,
    *,
    refresh_hours: int,
    now: datetime | None = None,
) -> S3BucketStatus | None:
    """Check the bucket unless a fresh row exists. Returns the row; None if the check broke."""
    now = now or datetime.now(UTC)
    row = session.get(S3BucketStatus, bucket)
    if row is not None and _aware(row.checked_at) > now - timedelta(hours=refresh_hours):
        return row
    try:
        status = check_bucket(client, bucket)
    except Exception:
        logger.warning("bucket status check failed for %s", bucket, exc_info=True)
        return row
    if row is None:
        row = S3BucketStatus(bucket=bucket, checked_at=now)
        session.add(row)
    row.versioning = status.versioning
    row.noncurrent_expiration = status.noncurrent_expiration
    row.error = status.error
    row.checked_at = now
    session.flush()
    return row


def _expires_noncurrent(rule: dict[str, Any]) -> bool:
    if str(rule.get("Status", "")).lower() != "enabled":
        return False
    expiration = rule.get("NoncurrentVersionExpiration") or {}
    return bool(expiration.get("NoncurrentDays") or expiration.get("NewerNoncurrentVersions"))


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
        if code:
            return "AccessDenied" if code in _DENIED else str(code)
    return type(exc).__name__


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
