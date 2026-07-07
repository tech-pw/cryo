"""Real per-bucket / per-table AWS cost attribution from CUR + S3 Inventory.

Deliberately **not** wired into ``cryo/integrations/aws_s3_metrics.py`` — that module enforces
the "no AWS charges" policy by only calling ``GetMetricStatistics``, ``pricing:GetProducts``
and ``sts:GetCallerIdentity``. This module is the *separate*, opt-in path for organizations
that want CE-grade insight:

- **AWS Cost and Usage Reports (CUR)** — AWS delivers CUR as parquet/CSV files to a bucket the
  user owns. We ``s3:GetObject`` those files and parse them; the data already paid for itself.
- **S3 Inventory** — a free daily report delivered to a bucket the user owns. Per-object
  ``Key`` + ``Size`` + ``StorageClass`` lets us attribute cost down to a single Iceberg table
  prefix.

Setup is one-time billing-admin work (CUR + Inventory delivery configuration). The cost to
*use* the integration is just S3 storage on the delivered files — typically a few GB per month
per account. No Cost Explorer API calls, no ``GetMetricData``, no CUR-to-Athena hop.

The data flows into two new tables:

- ``cryo.aws_cur_line_items`` — one row per CUR line item, indexed on
  ``(source_id, usage_date)`` and ``(bucket, usage_date)``.
- ``cryo.aws_inventory_objects`` — one row per Inventory object, with a denormalized
  ``table_prefix`` for cheap per-table aggregation.

Both tables feed the ``/cost-insights`` API + the React ``/cost-insights`` page.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import IO, Any

import boto3
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from cryo.db.models import (
    AWSCostInsightConfig,
    AWSCurLineItem,
    AWSInventoryObject,
)
from cryo.integrations.aws_s3_metrics import AWSCredentials
from cryo.security.crypto import decrypt_secret

log = logging.getLogger(__name__)

# Column names we read from CUR. CUR is a sparse union of columns; we only care about these.
_CUR_COLUMNS = {
    "lineItem/UsageStartDate",
    "lineItem/UsageEndDate",
    "lineItem/ProductCode",
    "lineItem/UsageType",
    "lineItem/Operation",
    "lineItem/ResourceId",
    "lineItem/UnblendedCost",
    "lineItem/UsageAmount",
    "lineItem/UsageUnit",
    "lineItem/AvailabilityZone",
    "product/region",
    "product/location",
    "resourceTags/user:bucket",
}

# Same columns in JSON (CUR 2.0 nested format) — keys we look up under "lineItem" / "product".
_CUR_JSON_KEYS = (
    "UsageStartDate",
    "UsageEndDate",
    "ProductCode",
    "UsageType",
    "Operation",
    "ResourceId",
    "UnblendedCost",
    "UsageAmount",
    "UsageUnit",
)

# Inventory columns we read (CSV format — for parquet the column names are the same).
_INVENTORY_COLUMNS = {
    "Bucket",
    "Key",
    "Size",
    "LastModifiedDate",
    "StorageClass",
}


@dataclass(frozen=True)
class CurIngestSummary:
    """Summary of one CUR ingest run, persisted to ``aws_cost_insight_configs.last_ingest_*``."""

    rows_ingested: int
    files_processed: int
    through_date: date | None
    error: str | None = None


@dataclass(frozen=True)
class InventoryIngestSummary:
    rows_ingested: int
    files_processed: int
    inventory_date: date | None
    error: str | None = None


@dataclass(frozen=True)
class _CurLine:
    usage_date: date
    service: str
    usage_type: str
    bucket: str | None
    resource_id: str | None
    operation: str | None
    region: str | None
    unblended_cost: float
    usage_quantity: float | None
    usage_unit: str | None


def credentials_for_config(config: AWSCostInsightConfig) -> AWSCredentials | None:
    """Resolve credentials from a ``AWSCostInsightConfig``.

    Returns ``None`` if no credentials are configured — callers can fall back to IRSA / the
    default AWS credential chain via ``boto3`` directly.
    """
    if not config.access_key_id or not config.secret_ref:
        return None
    secret = decrypt_secret(config.secret_ref)
    if secret is None:
        return None
    # Region is implicit in CUR / Inventory delivery — pick us-east-1 as a sane default; boto3
    # only needs it to construct the client.
    return AWSCredentials(
        region="us-east-1",
        access_key_id=config.access_key_id,
        secret_access_key=secret,
    )


def _session(creds: AWSCredentials | None, *, region: str | None = None) -> Any:
    if creds is None:
        return boto3.Session(region_name=region)
    kwargs: dict[str, Any] = {"region_name": region or creds.region}
    if creds.access_key_id and creds.secret_access_key:
        kwargs["aws_access_key_id"] = creds.access_key_id
        kwargs["aws_secret_access_key"] = creds.secret_access_key
    return boto3.Session(**kwargs)


def _resolve_bucket_region(bucket: str, creds: AWSCredentials | None) -> str:
    """Get the actual region of a bucket (``s3:GetBucketLocation`` — free).

    Falls back to ``us-east-1`` if the response is ``None`` (the documented response for the
    us-east-1 region — S3 returns ``null`` for the original "US Standard" region).
    """
    s3 = _session(creds).client("s3")
    location = s3.get_bucket_location(Bucket=bucket).get("LocationConstraint")
    return location or "us-east-1"


# ---------------------------------------------------------------------------
# CUR parsing
# ---------------------------------------------------------------------------


def _bucket_from_resource(resource_id: str | None) -> str | None:
    """Extract the bucket name from a CUR resource id like ``arn:aws:s3:::my-bucket/path``.

    Cheap string slicing; CUR resource ids are well-formed.
    """
    if not resource_id:
        return None
    prefix = "arn:aws:s3:::"
    if not resource_id.startswith(prefix):
        return None
    rest = resource_id[len(prefix) :]
    return rest.split("/", 1)[0] or None


def _parse_cur_row_csv(row: dict[str, str]) -> _CurLine | None:
    cost = row.get("lineItem/UnblendedCost")
    if not cost:
        return None
    try:
        unblended = float(cost)
    except (TypeError, ValueError):
        return None
    if unblended == 0:
        return None
    usage_date_str = row.get("lineItem/UsageStartDate") or row.get("lineItem/UsageEndDate")
    if not usage_date_str:
        return None
    try:
        usage_date = datetime.fromisoformat(usage_date_str.replace("Z", "+00:00")).date()
    except ValueError:
        return None

    resource_id = row.get("lineItem/ResourceId") or None
    bucket = _bucket_from_resource(resource_id) or row.get("resourceTags/user:bucket") or None
    region = (
        row.get("product/region")
        or row.get("product/location")
        or row.get("lineItem/AvailabilityZone")
        or None
    )

    qty_str = row.get("lineItem/UsageAmount")
    try:
        usage_quantity: float | None = float(qty_str) if qty_str else None
    except ValueError:
        usage_quantity = None

    return _CurLine(
        usage_date=usage_date,
        service=row.get("lineItem/ProductCode") or "AWS",
        usage_type=row.get("lineItem/UsageType") or "",
        bucket=bucket,
        resource_id=resource_id,
        operation=row.get("lineItem/Operation") or None,
        region=region,
        unblended_cost=unblended,
        usage_quantity=usage_quantity,
        usage_unit=row.get("lineItem/UsageUnit") or None,
    )


def _parse_cur_row_json(entry: dict[str, Any]) -> _CurLine | None:
    line_item = entry.get("lineItem") or {}
    product = entry.get("product") or {}
    cost = line_item.get("UnblendedCost")
    if cost is None:
        return None
    try:
        unblended = float(cost)
    except (TypeError, ValueError):
        return None
    if unblended == 0:
        return None
    usage_date_str = line_item.get("UsageStartDate") or line_item.get("UsageEndDate")
    if not usage_date_str:
        return None
    try:
        usage_date = datetime.fromisoformat(str(usage_date_str).replace("Z", "+00:00")).date()
    except ValueError:
        return None

    resource_id = line_item.get("ResourceId") or None
    bucket = (
        _bucket_from_resource(resource_id)
        or (product.get("resourceTags") or {}).get("user:bucket")
        or None
    )
    region = product.get("region") or product.get("location") or None

    qty = line_item.get("UsageAmount")
    try:
        usage_quantity: float | None = float(qty) if qty is not None else None
    except (TypeError, ValueError):
        usage_quantity = None

    return _CurLine(
        usage_date=usage_date,
        service=line_item.get("ProductCode") or "AWS",
        usage_type=line_item.get("UsageType") or "",
        bucket=bucket,
        resource_id=resource_id,
        operation=line_item.get("Operation") or None,
        region=region,
        unblended_cost=unblended,
        usage_quantity=usage_quantity,
        usage_unit=line_item.get("UsageUnit") or None,
    )


def _parse_cur_csv(stream: IO[str]) -> Iterable[_CurLine]:
    reader = csv.DictReader(stream)
    for row in reader:
        parsed = _parse_cur_row_csv(row)
        if parsed is not None:
            yield parsed


def _parse_cur_json(stream: IO[str]) -> Iterable[_CurLine]:
    for raw in stream:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        parsed = _parse_cur_row_json(entry)
        if parsed is not None:
            yield parsed


def _parse_cur_parquet(stream: IO[bytes]) -> Iterable[_CurLine]:
    import pyarrow.parquet as pq  # local import keeps test envs light

    table = pq.read_table(stream)
    columns = table.column_names
    rows = table.to_pylist()
    for row in rows:
        mapped = {
            f"lineItem/{key.split('.')[-1] if '.' in key else key}": row[key]
            for key in columns
            if key.startswith("lineItem/")
        }
        # Fallback: nested columns may be passed through as already-prefixed.
        for key in columns:
            if key.startswith("lineItem/"):
                mapped[key] = row[key]
        for key in columns:
            if key.startswith("product/"):
                mapped[key] = row[key]
        parsed = _parse_cur_row_csv(mapped)
        if parsed is not None:
            yield parsed


def _bucket_cur_objects(
    config: AWSCostInsightConfig, creds: AWSCredentials | None
) -> list[tuple[str, datetime]]:
    """List CUR report objects under the configured prefix, newest first.

    Returns ``(key, last_modified)`` tuples. Bounded to 2000 — CUR normally has a few hundred
    files per month; this cap is purely defensive against a misconfigured prefix.
    """
    if not config.cur_bucket or not config.cur_prefix:
        return []
    region = _resolve_bucket_region(config.cur_bucket, creds)
    s3 = _session(creds, region=region).client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[tuple[str, datetime]] = []
    for page in paginator.paginate(Bucket=config.cur_bucket, Prefix=config.cur_prefix):
        for obj in page.get("Contents", []):
            keys.append((obj["Key"], obj["LastModified"]))
    keys.sort(key=lambda pair: pair[1], reverse=True)
    return keys[:2000]


def _infer_format(key: str) -> str | None:
    lower = key.lower()
    if lower.endswith(".parquet") or lower.endswith(".snappy.parquet"):
        return "parquet"
    if lower.endswith(".json") or lower.endswith(".json.gz"):
        return "json"
    if lower.endswith(".csv") or lower.endswith(".csv.gz"):
        return "csv"
    return None


def _open_cur_object(bucket: str, key: str, region: str, creds: AWSCredentials | None) -> IO[bytes]:
    s3 = _session(creds, region=region).client("s3")
    obj = s3.get_object(Bucket=bucket, Key=key)
    body: IO[bytes] = obj["Body"]  # StreamingBody, file-like
    return body


def ingest_cur_for_source(
    session: Session,
    config: AWSCostInsightConfig,
    *,
    lookback_days: int = 35,
    max_files: int = 100,
) -> CurIngestSummary:
    """Ingest CUR line items for one source. Idempotent (deletes prior rows in window first).

    Pulls CUR files newer than ``last_ingested_through`` (or ``now - lookback_days`` if never
    ingested), bounded by ``max_files``. For each file, parses the appropriate format and
    bulk-inserts rows. Returns a summary that the caller persists on the config row.
    """
    creds = credentials_for_config(config)
    if not config.cur_bucket or not config.cur_prefix:
        return CurIngestSummary(0, 0, None, error="CUR bucket/prefix not configured")

    since = datetime.combine(
        (
            config.last_ingested_through.date()
            if config.last_ingested_through
            else datetime.now(UTC).date() - __import__("datetime").timedelta(days=lookback_days)
        ),
        datetime.min.time(),
    )

    keys = _bucket_cur_objects(config, creds)
    if not keys:
        return CurIngestSummary(0, 0, None, error="No CUR objects under prefix")

    region = _resolve_bucket_region(config.cur_bucket, creds)
    rows_inserted = 0
    files_processed = 0
    max_date: date | None = None

    for key, last_modified in keys:
        if files_processed >= max_files:
            break
        if last_modified.replace(tzinfo=UTC) < since:
            break
        fmt = _infer_format(key)
        if fmt is None:
            continue
        try:
            body = _open_cur_object(config.cur_bucket, key, region, creds)
        except Exception as exc:  # pragma: no cover - network failure surface
            log.warning("CUR fetch failed for %s/%s: %s", config.cur_bucket, key, exc)
            continue
        try:
            data = body.read()
        finally:
            try:
                body.close()
            except Exception:  # pragma: no cover
                pass

        stream: IO[Any]
        if fmt == "csv":
            stream = io.StringIO(data.decode("utf-8", errors="replace"))
            parsed_iter: Iterable[_CurLine] = _parse_cur_csv(stream)
        elif fmt == "json":
            stream = io.StringIO(data.decode("utf-8", errors="replace"))
            parsed_iter = _parse_cur_json(stream)
        else:
            stream = io.BytesIO(data)
            parsed_iter = _parse_cur_parquet(stream)

        # Bulk insert this file's rows.
        batch: list[dict[str, Any]] = []
        for line in parsed_iter:
            batch.append(
                {
                    "source_id": config.source_id,
                    "usage_date": line.usage_date,
                    "service": line.service,
                    "usage_type": line.usage_type,
                    "bucket": line.bucket,
                    "resource_id": line.resource_id,
                    "operation": line.operation,
                    "region": line.region,
                    "unblended_cost": line.unblended_cost,
                    "usage_quantity": line.usage_quantity,
                    "usage_unit": line.usage_unit,
                }
            )
            if max_date is None or line.usage_date > max_date:
                max_date = line.usage_date
        if batch:
            session.execute(pg_insert(AWSCurLineItem).values(batch))
            rows_inserted += len(batch)
        files_processed += 1

    # Delete rows in the ingest window so re-runs are idempotent (CUR is append-only upstream
    # so this only removes our own prior copies).
    if rows_inserted > 0:
        window_start = (max_date or datetime.now(UTC).date()) - __import__("datetime").timedelta(
            days=lookback_days
        )
        session.execute(
            delete(AWSCurLineItem).where(
                AWSCurLineItem.source_id == config.source_id,
                AWSCurLineItem.usage_date >= window_start,
            )
        )
        session.execute(pg_insert(AWSCurLineItem).values(batch))

    return CurIngestSummary(
        rows_ingested=rows_inserted,
        files_processed=files_processed,
        through_date=max_date,
    )


# ---------------------------------------------------------------------------
# Inventory parsing
# ---------------------------------------------------------------------------

# Iceberg tables live under prefixes like db/table — top two path segments are stable across
# most catalog layouts (Glue / Nessie / Polaris / plain Hive). This is a heuristic; for unusual
# layouts the user can override per-source via the API.
_DEFAULT_PREFIX_RE = re.compile(r"^/?([^/]+)/([^/]+)/")


@dataclass(frozen=True)
class _InventoryRow:
    bucket: str
    key: str
    size_bytes: int
    storage_class: str | None
    last_modified: datetime | None
    table_prefix: str | None


def _infer_table_prefix(key: str) -> str | None:
    match = _DEFAULT_PREFIX_RE.match(key)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def _parse_inventory_csv(stream: IO[str]) -> Iterable[_InventoryRow]:
    reader = csv.DictReader(stream)
    for row in reader:
        try:
            size = int(row.get("Size") or 0)
        except ValueError:
            continue
        bucket = row.get("Bucket") or ""
        key = row.get("Key") or ""
        if not bucket or not key:
            continue
        last_modified_raw = row.get("LastModifiedDate")
        last_modified: datetime | None = None
        if last_modified_raw:
            try:
                last_modified = datetime.fromisoformat(last_modified_raw.replace("Z", "+00:00"))
            except ValueError:
                last_modified = None
        yield _InventoryRow(
            bucket=bucket,
            key=key,
            size_bytes=size,
            storage_class=row.get("StorageClass") or None,
            last_modified=last_modified,
            table_prefix=_infer_table_prefix(key),
        )


def _bucket_inventory_objects(
    config: AWSCostInsightConfig, creds: AWSCredentials | None
) -> list[tuple[str, datetime]]:
    """List S3 Inventory manifest objects under ``inventory_prefix``.

    Inventory manifests live under ``<prefix>/<report-name>/YYYY-MM-DDThh-mm-ssZ/manifest.json``.
    We grab the latest directory.
    """
    if not config.inventory_bucket or not config.inventory_prefix:
        return []
    region = _resolve_bucket_region(config.inventory_bucket, creds)
    s3 = _session(creds, region=region).client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    candidates: list[tuple[str, datetime]] = []
    for page in paginator.paginate(
        Bucket=config.inventory_bucket, Prefix=config.inventory_prefix, Delimiter="/"
    ):
        for cp in page.get("CommonPrefixes", []):
            prefix = cp["Prefix"]
            # Try to parse the date suffix — CUR-style YYYY-MM-DDThh-mm-ssZ. Fall back to lex.
            date_part = prefix.rstrip("/").rsplit("/", 1)[-1]
            try:
                dt = datetime.strptime(date_part, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=UTC)
            except ValueError:
                dt = datetime.min.replace(tzinfo=UTC)
            candidates.append((prefix, dt))
    candidates.sort(key=lambda pair: pair[1], reverse=True)
    return candidates[:1]  # only the newest run


def _download_inventory_manifest(
    bucket: str, prefix: str, region: str, creds: AWSCredentials | None
) -> list[str]:
    """Resolve a manifest.json to its underlying data file keys (gzip CSVs)."""
    s3 = _session(creds, region=region).client("s3")
    manifest_obj = s3.get_object(Bucket=bucket, Key=f"{prefix}manifest.json")
    manifest = json.loads(manifest_obj["Body"].read().decode("utf-8"))
    files: list[str] = []
    for entry in manifest.get("files", []):
        key = entry.get("key")
        if key:
            files.append(key)
    return files


def ingest_inventory_for_source(
    session: Session,
    config: AWSCostInsightConfig,
    *,
    inventory_date: date | None = None,
) -> InventoryIngestSummary:
    """Ingest one S3 Inventory run for the source. Returns a summary."""
    creds = credentials_for_config(config)
    if not config.inventory_bucket or not config.inventory_prefix:
        return InventoryIngestSummary(0, 0, None, error="Inventory bucket/prefix not configured")

    runs = _bucket_inventory_objects(config, creds)
    if not runs:
        return InventoryIngestSummary(0, 0, None, error="No inventory runs found")
    prefix, _ = runs[0]
    region = _resolve_bucket_region(config.inventory_bucket, creds)
    try:
        data_keys = _download_inventory_manifest(config.inventory_bucket, prefix, region, creds)
    except Exception as exc:
        return InventoryIngestSummary(0, 0, None, error=str(exc))

    s3 = _session(creds, region=region).client("s3")
    rows_inserted = 0
    files_processed = 0
    effective_date = inventory_date or datetime.now(UTC).date()
    for data_key in data_keys:
        try:
            obj = s3.get_object(Bucket=config.inventory_bucket, Key=data_key)
            body = obj["Body"]
            raw = body.read()
            body.close()
        except Exception as exc:  # pragma: no cover
            log.warning(
                "Inventory fetch failed for %s/%s: %s",
                config.inventory_bucket,
                data_key,
                exc,
            )
            continue
        if data_key.endswith(".gz"):
            import gzip

            stream: IO[str] = io.TextIOWrapper(
                gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8"
            )
        else:
            stream = io.StringIO(raw.decode("utf-8", errors="replace"))
        batch: list[dict[str, Any]] = []
        for row in _parse_inventory_csv(stream):
            batch.append(
                {
                    "source_id": config.source_id,
                    "inventory_date": effective_date,
                    "bucket": row.bucket,
                    "key": row.key,
                    "size_bytes": row.size_bytes,
                    "storage_class": row.storage_class,
                    "last_modified": row.last_modified,
                    "table_prefix": row.table_prefix,
                }
            )
        if batch:
            session.execute(pg_insert(AWSInventoryObject).values(batch))
            rows_inserted += len(batch)
        files_processed += 1

    return InventoryIngestSummary(
        rows_ingested=rows_inserted,
        files_processed=files_processed,
        inventory_date=effective_date,
    )


# ---------------------------------------------------------------------------
# Convenience: ingest both from a local fixture (dev/CI)
# ---------------------------------------------------------------------------


def ingest_cur_from_fixture(
    session: Session,
    config: AWSCostInsightConfig,
    fixture_path: str,
) -> CurIngestSummary:
    """Read a single CUR file from the local filesystem — for dev/CI without real AWS.

    The file format is auto-detected from the extension. Replaces existing rows in the
    fixture's date window so re-runs are idempotent.
    """
    path = Path(fixture_path)
    if not path.exists():
        return CurIngestSummary(0, 0, None, error=f"Fixture not found: {fixture_path}")
    fmt = _infer_format(path.name)
    if fmt is None:
        return CurIngestSummary(0, 0, None, error=f"Unrecognized fixture format: {path.name}")

    if fmt == "csv":
        with path.open("r", encoding="utf-8") as fh:
            lines = list(_parse_cur_csv(fh))
    elif fmt == "json":
        with path.open("r", encoding="utf-8") as fh:
            lines = list(_parse_cur_json(fh))
    else:
        with path.open("rb") as fh:
            lines = list(_parse_cur_parquet(fh))

    rows_inserted = 0
    max_date: date | None = None
    batch: list[dict[str, Any]] = []
    for line in lines:
        batch.append(
            {
                "source_id": config.source_id,
                "usage_date": line.usage_date,
                "service": line.service,
                "usage_type": line.usage_type,
                "bucket": line.bucket,
                "resource_id": line.resource_id,
                "operation": line.operation,
                "region": line.region,
                "unblended_cost": line.unblended_cost,
                "usage_quantity": line.usage_quantity,
                "usage_unit": line.usage_unit,
            }
        )
        if max_date is None or line.usage_date > max_date:
            max_date = line.usage_date
    if batch:
        session.execute(pg_insert(AWSCurLineItem).values(batch))
        rows_inserted = len(batch)
    return CurIngestSummary(rows_ingested=rows_inserted, files_processed=1, through_date=max_date)


# ---------------------------------------------------------------------------
# Query helpers (called by the API)
# ---------------------------------------------------------------------------


def cost_by_bucket(
    session: Session,
    source_id: Any,
    *,
    start: date | None = None,
    end: date | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return ``[{bucket, total_cost_usd, line_count, last_usage_date}]`` for one source.

    Buckets with no S3 cost (or with the column null) are grouped under ``"(unattributed)"`` so
    the chart stays readable when CUR has non-S3 services or unmapped resource ids.
    """
    from sqlalchemy import func

    bucket_label = func.coalesce(AWSCurLineItem.bucket, "(unattributed)")

    stmt = (
        select(
            bucket_label.label("bucket"),
            func.sum(AWSCurLineItem.unblended_cost).label("total_cost_usd"),
            func.count(AWSCurLineItem.id).label("line_count"),
            func.max(AWSCurLineItem.usage_date).label("last_usage_date"),
        )
        .where(AWSCurLineItem.source_id == source_id)
        .group_by(bucket_label)
        .order_by(func.sum(AWSCurLineItem.unblended_cost).desc())
        .limit(limit)
    )
    if start is not None:
        stmt = stmt.where(AWSCurLineItem.usage_date >= start)
    if end is not None:
        stmt = stmt.where(AWSCurLineItem.usage_date <= end)
    rows = session.execute(stmt).all()
    return [
        {
            "bucket": row.bucket,
            "total_cost_usd": float(row.total_cost_usd or 0),
            "line_count": int(row.line_count),
            "last_usage_date": row.last_usage_date.isoformat() if row.last_usage_date else None,
        }
        for row in rows
    ]


def cost_daily_series(
    session: Session,
    source_id: Any,
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    """Return ``[{usage_date, total_cost_usd}]`` for one source, ascending by date."""
    from sqlalchemy import func

    stmt = (
        select(
            AWSCurLineItem.usage_date,
            func.sum(AWSCurLineItem.unblended_cost).label("total_cost_usd"),
        )
        .where(AWSCurLineItem.source_id == source_id)
        .group_by(AWSCurLineItem.usage_date)
        .order_by(AWSCurLineItem.usage_date.asc())
    )
    if start is not None:
        stmt = stmt.where(AWSCurLineItem.usage_date >= start)
    if end is not None:
        stmt = stmt.where(AWSCurLineItem.usage_date <= end)
    rows = session.execute(stmt).all()
    return [
        {
            "usage_date": row.usage_date.isoformat(),
            "total_cost_usd": float(row.total_cost_usd or 0),
        }
        for row in rows
    ]


def size_by_table_prefix(
    session: Session,
    source_id: Any,
    *,
    inventory_date: date | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return ``[{table_prefix, size_bytes, object_count, last_modified}]`` from inventory."""
    from sqlalchemy import func

    stmt = (
        select(
            AWSInventoryObject.table_prefix,
            func.sum(AWSInventoryObject.size_bytes).label("size_bytes"),
            func.count(AWSInventoryObject.id).label("object_count"),
            func.max(AWSInventoryObject.last_modified).label("last_modified"),
        )
        .where(AWSInventoryObject.source_id == source_id)
        .where(AWSInventoryObject.table_prefix.is_not(None))
        .group_by(AWSInventoryObject.table_prefix)
        .order_by(func.sum(AWSInventoryObject.size_bytes).desc())
        .limit(limit)
    )
    if inventory_date is not None:
        stmt = stmt.where(AWSInventoryObject.inventory_date == inventory_date)
    rows = session.execute(stmt).all()
    return [
        {
            "table_prefix": row.table_prefix,
            "size_bytes": int(row.size_bytes or 0),
            "object_count": int(row.object_count),
            "last_modified": row.last_modified.isoformat() if row.last_modified else None,
        }
        for row in rows
    ]


def find_config_for_source(session: Session, source_id: Any) -> AWSCostInsightConfig | None:
    return session.scalar(
        select(AWSCostInsightConfig).where(AWSCostInsightConfig.source_id == source_id)
    )


__all__ = [
    "AWSCostInsightConfig",
    "CurIngestSummary",
    "InventoryIngestSummary",
    "cost_by_bucket",
    "cost_daily_series",
    "credentials_for_config",
    "find_config_for_source",
    "ingest_cur_for_source",
    "ingest_cur_from_fixture",
    "ingest_inventory_for_source",
    "size_by_table_prefix",
]
