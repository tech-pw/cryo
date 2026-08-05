from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.config import Config

from cryo.config import Settings
from cryo.db.models import TableCost

S3_LIST_KEYS_PER_PAGE = 1000


@dataclass(frozen=True)
class OrphanScanResult:
    orphan_bytes: int
    listed_bytes: int
    listed_keys: int
    last_full_cost_scan_at: datetime | None
    skipped_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrphanScanPreflight:
    estimated_list_requests: int
    estimated_cost_usd: float
    estimated_files_to_scan: int
    partition_depth: int
    warning: str | None = None


def estimate_orphan_scan_cost(
    *,
    total_files: int,
    partition_depth: int,
    settings: Settings,
) -> OrphanScanPreflight:
    """Move 8: preflight S3 LIST request cost estimate before running an orphan scan.

    S3 LIST is paginated at 1,000 keys per request. Each partition prefix incurs one
    additional LIST request to enumerate CommonPrefixes. The estimate uses the table's
    total_file_count from the most recent scan (already collected, no extra S3 calls).

    The orphan scan itself is bounded by cost_scan_max_list_keys, so the estimate is
    capped accordingly.
    """
    capped_files = min(total_files, settings.cost_scan_max_list_keys)
    list_page_requests = (capped_files + S3_LIST_KEYS_PER_PAGE - 1) // S3_LIST_KEYS_PER_PAGE
    partition_requests = partition_depth
    total_requests = list_page_requests + partition_requests

    estimated_cost = (total_requests / 1000.0) * settings.s3_list_usd_per_1k

    warning: str | None = None
    if total_files > settings.cost_scan_max_list_keys:
        warning = (
            f"Table has ~{total_files:,} estimated files; orphan scan is capped at "
            f"{settings.cost_scan_max_list_keys:,} keys by cost_scan_max_list_keys. "
            f"Actual S3 request count may be higher for uncovered partitions."
        )
    elif estimated_cost > 1.0:
        warning = (
            f"Estimated S3 request cost ${estimated_cost:.2f} exceeds $1.00. "
            f"Consider increasing orphan_retention_days to reduce the scan scope, "
            f"or run during off-peak hours."
        )

    return OrphanScanPreflight(
        estimated_list_requests=total_requests,
        estimated_cost_usd=round(estimated_cost, 6),
        estimated_files_to_scan=capped_files,
        partition_depth=partition_depth,
        warning=warning,
    )


def should_run_orphan_scan(
    previous_cost: TableCost | None,
    scan_at: datetime,
    settings: Settings,
    *,
    force: bool = False,
) -> bool:
    if force:
        return True
    if settings.cost_full_scan_every_hours <= 0:
        return False
    if previous_cost is None or previous_cost.last_full_cost_scan_at is None:
        return True
    cadence = timedelta(hours=settings.cost_full_scan_every_hours)
    return scan_at - previous_cost.last_full_cost_scan_at >= cadence


def scan_orphans(
    table_obj: object,
    *,
    source_config: dict[str, Any],
    referenced_file_paths: dict[str, int],
    scan_at: datetime,
    settings: Settings,
) -> OrphanScanResult:
    location = table_location(table_obj)
    if not location:
        return OrphanScanResult(
            orphan_bytes=0,
            listed_bytes=0,
            listed_keys=0,
            last_full_cost_scan_at=None,
            skipped_reason="no_location",
        )

    bucket, prefix = parse_s3_location(location)
    if bucket is None:
        return OrphanScanResult(
            orphan_bytes=0,
            listed_bytes=0,
            listed_keys=0,
            last_full_cost_scan_at=None,
            skipped_reason="unsupported_location",
            raw={"location": location},
        )

    client = _s3_client(source_config)
    min_modified = scan_at - timedelta(hours=settings.cost_orphan_min_age_hours)
    listed_bytes = 0
    orphan_bytes = 0
    listed_keys = 0
    paginator = client.get_paginator("list_objects_v2")

    # Scheme-agnostic (see _normalize_s3_path) -- referenced_file_paths keys come from manifest
    # file_path entries, which preserve whatever scheme the writer used (s3a/s3n are common for
    # Spark-written tables); comparing against a hardcoded "s3://" reconstruction here would
    # silently fail to match anything for those tables. Built once, not per-object.
    normalized_referenced = {_normalize_s3_path(path) for path in referenced_file_paths}

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            listed_keys += 1
            if listed_keys > settings.cost_scan_max_list_keys:
                return OrphanScanResult(
                    orphan_bytes=0,
                    listed_bytes=listed_bytes,
                    listed_keys=listed_keys,
                    last_full_cost_scan_at=None,
                    skipped_reason="max_list_keys",
                    raw={"bucket": bucket, "prefix": prefix},
                )
            key = str(obj["Key"])
            size = int(obj.get("Size", 0) or 0)
            last_modified = obj.get("LastModified")
            if isinstance(last_modified, datetime):
                modified = (
                    last_modified if last_modified.tzinfo else last_modified.replace(tzinfo=UTC)
                )
                if modified > min_modified:
                    continue
            listed_bytes += size
            if f"{bucket}/{key}" not in normalized_referenced:
                orphan_bytes += size

    return OrphanScanResult(
        orphan_bytes=orphan_bytes,
        listed_bytes=listed_bytes,
        listed_keys=listed_keys,
        last_full_cost_scan_at=scan_at,
        raw={"bucket": bucket, "prefix": prefix},
    )


def carry_forward_orphans(
    previous_cost: TableCost | None,
) -> tuple[int | None, int | None, datetime | None]:
    if previous_cost is None:
        return None, None, None
    return (
        previous_cost.orphan_bytes,
        previous_cost.listed_bytes,
        previous_cost.last_full_cost_scan_at,
    )


def table_location(table_obj: object) -> str | None:
    """Resolve the live S3 location of an Iceberg table object (its root, not the data/ subpath)."""
    location = getattr(table_obj, "location", None)
    if callable(location):
        try:
            location = location()
        except TypeError:
            location = None
    if location:
        return str(location)
    metadata = getattr(table_obj, "metadata", None)
    if metadata is not None:
        meta_location = getattr(metadata, "location", None)
        if meta_location:
            return str(meta_location)
    return None


_S3_SCHEMES = {"s3", "s3a", "s3n"}


def _normalize_s3_path(path: str) -> str:
    """Scheme-agnostic ``bucket/key`` form of an S3 URI, for equality comparison only.

    Manifest ``file_path`` entries preserve whatever scheme the writer used verbatim (s3a/s3n
    are the Hadoop/Spark-ecosystem conventions, common for Hive Metastore-backed tables written
    via Spark rather than AWS-native tooling -- see ``parse_s3_location``'s docstring, which
    already documents this exact scheme-equivalence issue for bucket/prefix discovery). Comparing
    a listed object's reconstructed URI against ``referenced_file_paths`` only works if both
    sides use the *same* scheme string; hardcoding one side to a single scheme silently breaks
    the comparison for every table using a different one -- every listed file then looks
    unreferenced, producing a false 100% "orphan" rate. Found 2026-08-05: every table with a
    completed orphan scan in production (11,281 tables, ~296TB) showed exactly
    ``orphan_bytes == listed_bytes``, i.e. this comparison was silently failing for 100% of
    objects on every scan, because the deployment's tables use ``s3a://`` while the listing side
    reconstructed ``s3://`` unconditionally.
    """
    parsed = urlparse(path)
    if parsed.scheme in _S3_SCHEMES:
        return f"{parsed.netloc}/{parsed.path.lstrip('/')}"
    return path


def parse_s3_location(location: str) -> tuple[str | None, str]:
    """Bucket + data-prefix from a table location URI.

    Accepts "s3://", "s3a://", and "s3n://" as equivalent -- they address the same bucket/key
    structure and differ only in which client library historically used the scheme (s3a/s3n are
    the Hadoop/Spark-ecosystem conventions, common for Hive Metastore-backed tables written via
    Spark rather than AWS-native tooling). Treating only "s3" as valid silently broke bucket
    discovery (S3LogConfigPanel) and orphan-file scanning (skipped_reason="unsupported_location"
    for every table) on any source using s3a://, with no error surfaced anywhere.
    """
    parsed = urlparse(location)
    if parsed.scheme in _S3_SCHEMES:
        bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"
        data_prefix = f"{prefix}data/" if prefix else "data/"
        return bucket, data_prefix
    return None, ""


def _s3_client(source_config: dict[str, Any]) -> Any:
    endpoint = source_config.get("s3.endpoint")
    access_key = source_config.get("s3.access-key-id")
    secret_key = source_config.get("s3.secret-access-key")
    region = source_config.get("s3.region", "us-east-1")
    path_style = str(source_config.get("s3.path-style-access", "false")).lower() in {
        "true",
        "1",
        "yes",
    }
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(s3={"addressing_style": "path" if path_style else "auto"}),
    )
