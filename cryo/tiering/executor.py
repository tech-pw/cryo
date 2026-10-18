"""In-place S3 storage-class changes for archive and restore.

Each object is copied onto its own key with a new ``StorageClass``, so no Iceberg path changes
and no metadata commit is needed. Only instant-access classes are allowed: Glacier Flexible,
Deep Archive and the Intelligent-Tiering archive tiers fail Iceberg reads with
``InvalidObjectState`` (see docs/ARCHIVE_TIERING_RESEARCH_2026-10-01.md §4.5).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

INSTANT_TARGETS = frozenset({"STANDARD", "INTELLIGENT_TIERING", "STANDARD_IA", "GLACIER_IR"})
ARCHIVED_CLASSES = frozenset({"STANDARD_IA", "GLACIER_IR"})
_OFFLINE = frozenset({"GLACIER", "DEEP_ARCHIVE"})
# Standard-IA and Glacier IR bill every object as at least 128 KiB, and Intelligent-Tiering
# never monitors objects under it, so moving them costs more than it saves.
MIN_OBJECT_BYTES = 128 * 1024
# Single-call CopyObject limit. Larger objects need a multipart copy, which this version skips.
MAX_OBJECT_BYTES = 5 * 1024**3
_MAX_ERRORS = 20
_BUCKET_CHECK_PURPOSE = "Check the bucket is safe to re-tier"
_S3_SCHEMES = {"s3", "s3a", "s3n"}

TARGET_LABELS = {
    "STANDARD": "Standard",
    "INTELLIGENT_TIERING": "Intelligent-Tiering",
    "STANDARD_IA": "Standard-IA",
    "GLACIER_IR": "Glacier Instant Retrieval",
}


@dataclass(frozen=True)
class ObjectRef:
    bucket: str
    key: str
    size: int | None = None


@dataclass
class TransitionResult:
    total: int = 0
    moved: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_moved: int = 0
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        if len(self.errors) < _MAX_ERRORS:
            self.errors.append(message)


@dataclass(frozen=True)
class PermissionCheck:
    action: str
    status: str  # "ok" | "missing" | "unchecked"
    purpose: str


@dataclass(frozen=True)
class BucketGuardrail:
    name: str
    status: str  # "pass" | "block" | "warn" | "unknown"
    detail: str


def parse_s3_uri(uri: str) -> ObjectRef | None:
    parsed = urlparse(uri)
    if parsed.scheme not in _S3_SCHEMES or not parsed.netloc:
        return None
    key = parsed.path.lstrip("/")
    return ObjectRef(parsed.netloc, key) if key else None


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        return str(response.get("Error", {}).get("Code", ""))
    return ""


def _denied(exc: BaseException) -> bool:
    return _error_code(exc) in {"AccessDenied", "AllAccessDisabled", "403", "Forbidden"}


def _current_class(head: Mapping[str, Any]) -> str:
    return str(head.get("StorageClass") or "STANDARD")


def _copy_args(ref: ObjectRef, head: Mapping[str, Any], target: str) -> dict[str, Any]:
    args: dict[str, Any] = {
        "Bucket": ref.bucket,
        "Key": ref.key,
        "CopySource": {"Bucket": ref.bucket, "Key": ref.key},
        "StorageClass": target,
        "MetadataDirective": "COPY",
        "TaggingDirective": "COPY",
    }
    etag = head.get("ETag")
    if etag:
        args["CopySourceIfMatch"] = etag
    if head.get("ServerSideEncryption") == "aws:kms":
        args["ServerSideEncryption"] = "aws:kms"
        if head.get("SSEKMSKeyId"):
            args["SSEKMSKeyId"] = head["SSEKMSKeyId"]
        if head.get("BucketKeyEnabled") is not None:
            args["BucketKeyEnabled"] = bool(head["BucketKeyEnabled"])
    return args


def _transition_one(client: Any, ref: ObjectRef, target: str) -> tuple[str, int, str | None]:
    """Returns (outcome, bytes, error) where outcome is moved, skipped or failed."""
    try:
        head = client.head_object(Bucket=ref.bucket, Key=ref.key)
    except Exception as exc:
        if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
            return "skipped", 0, None
        return "failed", 0, f"{ref.key}: HeadObject {_error_code(exc) or type(exc).__name__}"
    size = int(head.get("ContentLength") or 0)
    current = _current_class(head)
    if current == target:
        return "skipped", 0, None
    if current in _OFFLINE or head.get("ArchiveStatus"):
        return "failed", 0, f"{ref.key}: in {current}, needs an S3 restore first"
    if head.get("SSECustomerAlgorithm"):
        return "failed", 0, f"{ref.key}: encrypted with a customer key (SSE-C), not supported"
    if target != "STANDARD" and size < MIN_OBJECT_BYTES:
        return "skipped", 0, None
    if size > MAX_OBJECT_BYTES:
        return "failed", 0, f"{ref.key}: larger than 5 GB, not supported"
    try:
        client.copy_object(**_copy_args(ref, head, target))
        verified = client.head_object(Bucket=ref.bucket, Key=ref.key)
    except Exception as exc:
        return "failed", 0, f"{ref.key}: CopyObject {_error_code(exc) or type(exc).__name__}"
    if _current_class(verified) != target:
        return "failed", 0, f"{ref.key}: still {_current_class(verified)} after copy"
    return "moved", size, None


def transition_objects(
    client: Any,
    objects: Sequence[ObjectRef],
    target_class: str,
    *,
    max_workers: int = 16,
    on_progress: Callable[[TransitionResult], None] | None = None,
    progress_every: int = 200,
) -> TransitionResult:
    if target_class not in INSTANT_TARGETS:
        raise ValueError(f"{target_class} is not an instant-access storage class")
    result = TransitionResult(total=len(objects))
    if not objects:
        return result
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(objects)))) as pool:
        outcomes = pool.map(lambda ref: _transition_one(client, ref, target_class), objects)
        for index, (outcome, size, error) in enumerate(outcomes, start=1):
            if outcome == "moved":
                result.moved += 1
                result.bytes_moved += size
            elif outcome == "skipped":
                result.skipped += 1
            else:
                result.failed += 1
                if error:
                    result.add_error(error)
            if on_progress is not None and index % progress_every == 0:
                on_progress(result)
    return result


def check_permissions(client: Any, sample: ObjectRef | None) -> list[PermissionCheck]:
    """Probe what the credentials can do without changing anything.

    The PutObject probe copies the sample onto itself with nothing changed. S3 authorizes the
    request before validating it, so a permitted request fails with ``InvalidRequest`` ("copy
    an object to itself without changing...") and a forbidden one with ``AccessDenied``.
    """
    checks: list[PermissionCheck] = []
    head: Mapping[str, Any] | None = None
    if sample is None:
        checks.append(PermissionCheck("s3:GetObject", "unchecked", "Read each file's class"))
        checks.append(PermissionCheck("s3:PutObject", "unchecked", "Copy files to a new class"))
    else:
        try:
            head = client.head_object(Bucket=sample.bucket, Key=sample.key)
            checks.append(PermissionCheck("s3:GetObject", "ok", "Read each file's class"))
        except Exception as exc:
            status = "missing" if _denied(exc) else "unchecked"
            checks.append(PermissionCheck("s3:GetObject", status, "Read each file's class"))
        if head is None:
            checks.append(PermissionCheck("s3:PutObject", "unchecked", "Copy files to a new class"))
        else:
            # Any encryption argument counts as a change and would make S3 perform the copy.
            args = {
                key: value
                for key, value in _copy_args(sample, head, _current_class(head)).items()
                if key
                not in {
                    "CopySourceIfMatch",
                    "ServerSideEncryption",
                    "SSEKMSKeyId",
                    "BucketKeyEnabled",
                }
            }
            try:
                client.copy_object(**args)
                checks.append(PermissionCheck("s3:PutObject", "ok", "Copy files to a new class"))
            except Exception as exc:
                code = _error_code(exc)
                status = (
                    "ok" if code == "InvalidRequest" else "missing" if _denied(exc) else "unchecked"
                )
                checks.append(PermissionCheck("s3:PutObject", status, "Copy files to a new class"))
    return checks


_BUCKET_READS = (
    ("versioning", "s3:GetBucketVersioning", "get_bucket_versioning", ()),
    (
        "lifecycle",
        "s3:GetLifecycleConfiguration",
        "get_bucket_lifecycle_configuration",
        ("NoSuchLifecycleConfiguration",),
    ),
    (
        "object_lock",
        "s3:GetBucketObjectLockConfiguration",
        "get_object_lock_configuration",
        ("ObjectLockConfigurationNotFoundError",),
    ),
    (
        "replication",
        "s3:GetReplicationConfiguration",
        "get_bucket_replication",
        ("ReplicationConfigurationNotFoundError",),
    ),
)


def bucket_guardrails(
    client: Any, bucket: str
) -> tuple[list[BucketGuardrail], list[PermissionCheck]]:
    """Bucket settings that make an in-place copy unsafe or costlier than it looks, plus the
    read permissions those checks needed."""
    reads: dict[str, Mapping[str, Any] | None] = {}
    checks: list[PermissionCheck] = []
    for name, action, call, not_found in _BUCKET_READS:
        try:
            reads[name] = getattr(client, call)(Bucket=bucket)
            checks.append(PermissionCheck(action, "ok", "Check the bucket is safe to re-tier"))
        except Exception as exc:
            if _error_code(exc) in not_found:
                reads[name] = {}
                checks.append(PermissionCheck(action, "ok", _BUCKET_CHECK_PURPOSE))
            else:
                reads[name] = None
                status = "missing" if _denied(exc) else "unchecked"
                checks.append(PermissionCheck(action, status, _BUCKET_CHECK_PURPOSE))
    guardrails: list[BucketGuardrail] = []
    versioning = reads["versioning"]
    lifecycle = reads["lifecycle"]
    if versioning is None:
        guardrails.append(BucketGuardrail("versioning", "unknown", "Can't read bucket versioning."))
    elif versioning.get("Status") == "Enabled":
        expires = any(
            rule.get("Status") == "Enabled" and rule.get("NoncurrentVersionExpiration")
            for rule in (lifecycle or {}).get("Rules", [])
        )
        guardrails.append(
            BucketGuardrail(
                "versioning",
                "pass" if expires else "block",
                "Versioned, and a lifecycle rule expires old versions."
                if expires
                else "Versioned with no rule expiring old versions: every copy would leave the "
                "old Standard version billing.",
            )
        )
    else:
        guardrails.append(BucketGuardrail("versioning", "pass", "Not versioned."))
    lock = reads["object_lock"]
    if lock is None:
        guardrails.append(BucketGuardrail("object_lock", "unknown", "Can't read Object Lock."))
    elif (lock.get("ObjectLockConfiguration") or {}).get("ObjectLockEnabled") == "Enabled":
        guardrails.append(
            BucketGuardrail("object_lock", "block", "Object Lock is on; copies are refused.")
        )
    else:
        guardrails.append(BucketGuardrail("object_lock", "pass", "Object Lock is off."))
    replication = reads["replication"]
    if replication is None:
        guardrails.append(BucketGuardrail("replication", "unknown", "Can't read replication."))
    elif replication.get("ReplicationConfiguration"):
        guardrails.append(
            BucketGuardrail(
                "replication",
                "warn",
                "Replication is on: each copy replicates again and is billed at the destination.",
            )
        )
    else:
        guardrails.append(BucketGuardrail("replication", "pass", "No replication."))
    return guardrails, checks


def iam_policy(bucket: str) -> str:
    """Least-privilege policy for archive and restore on one bucket, as JSON for DevOps."""
    arn = f"arn:aws:s3:::{bucket}"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "CryoArchiveReadAndCopy",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:GetObjectTagging", "s3:PutObjectTagging"],
                "Resource": f"{arn}/*",
            },
            {
                "Sid": "CryoArchiveInstantClassesOnly",
                "Effect": "Allow",
                "Action": "s3:PutObject",
                "Resource": f"{arn}/*",
                "Condition": {"StringEquals": {"s3:x-amz-storage-class": sorted(INSTANT_TARGETS)}},
            },
            {
                "Sid": "CryoArchiveBucketChecks",
                "Effect": "Allow",
                "Action": [
                    "s3:GetBucketVersioning",
                    "s3:GetBucketObjectLockConfiguration",
                    "s3:GetReplicationConfiguration",
                    "s3:GetLifecycleConfiguration",
                ],
                "Resource": arn,
            },
        ],
    }
    return json.dumps(policy, indent=2)


__all__ = [
    "ARCHIVED_CLASSES",
    "INSTANT_TARGETS",
    "MAX_OBJECT_BYTES",
    "MIN_OBJECT_BYTES",
    "TARGET_LABELS",
    "BucketGuardrail",
    "ObjectRef",
    "PermissionCheck",
    "TransitionResult",
    "bucket_guardrails",
    "check_permissions",
    "iam_policy",
    "parse_s3_uri",
    "transition_objects",
]
