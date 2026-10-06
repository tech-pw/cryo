"""Free-tier-only AWS S3 cost/usage intelligence.

Deliberately limited to AWS APIs that cost nothing, so this integration can never generate an AWS
bill on its own:

- **S3's default daily CloudWatch storage metrics** (``BucketSizeBytes`` per storage class,
  ``NumberOfObjects``) — published automatically for every bucket with no setup and no charge.
  Read via ``cloudwatch:GetMetricStatistics`` specifically, *not* ``GetMetricData`` — AWS's
  CloudWatch free tier (1M API requests/month) explicitly excludes ``GetMetricData`` ("always
  charged" per AWS's own pricing page), while ``GetMetricStatistics`` falls under the free quota.
- **The AWS Price List API** (``pricing:GetProducts``) — free, no setup — for real
  region/storage-class unit prices, to replace a flat guessed $/GB-month rate.
- **``sts:GetCallerIdentity``** — free, used only to verify credentials work.

Explicitly NOT implemented, and not planned as a "disabled by default" option either: Cost
Explorer (`$0.01/API call <https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/>`_,
plus needs cost-allocation tags activated at the billing-admin level), CloudWatch S3 *request*
metrics filters (~$0.30/metric/month once enabled), and Cost and Usage Reports (needs
billing-admin setup). All three either cost money or need account permissions well beyond what a
bucket-scoped IAM read credential would have — out of scope for a self-hosted tool whose users
explicitly do not want to incur any AWS charges just to get better cost visibility.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3

logger = logging.getLogger(__name__)

# S3 storage classes CloudWatch reports BucketSizeBytes for, keyed by the StorageType dimension
# value. See https://docs.aws.amazon.com/AmazonS3/latest/userguide/metrics-dimensions.html
_STORAGE_TYPES = (
    "StandardStorage",
    "StandardIAStorage",
    "IntelligentTieringFAStorage",
    "IntelligentTieringIAStorage",
    "OneZoneIAStorage",
    "GlacierStorage",
    "DeepArchiveStorage",
    "ReducedRedundancyStorage",
)

# The Price List API's `location` filter wants a human region name, not a region code, and the
# API itself is only ever queryable from us-east-1 regardless of the bucket's own region. This
# covers the common regions; unmapped regions fall back gracefully (see fetch_s3_price_per_gb).
_REGION_NAMES: dict[str, str] = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "eu-central-1": "EU (Frankfurt)",
    "eu-north-1": "EU (Stockholm)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "sa-east-1": "South America (Sao Paulo)",
    "ca-central-1": "Canada (Central)",
}

PRICING_API_REGION = "us-east-1"  # Price List API endpoint itself; unrelated to the bucket's region


@dataclass(frozen=True)
class AWSCredentials:
    region: str
    access_key_id: str | None = None
    secret_access_key: str | None = None


@dataclass(frozen=True)
class AWSConnectionTestResult:
    ok: bool
    account_id: str | None = None
    arn: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class BucketStorageMetrics:
    bucket: str
    region: str
    measured_at: datetime | None
    size_bytes_by_storage_class: dict[str, int] = field(default_factory=dict)
    object_count: int | None = None

    @property
    def total_size_bytes(self) -> int:
        return sum(self.size_bytes_by_storage_class.values())


@dataclass(frozen=True)
class S3PriceInfo:
    region: str
    storage_class: str
    price_per_gb_month: float
    source: str = "aws_price_list_api"


@dataclass(frozen=True)
class S3RequestPriceInfo:
    """Per-(region, storage class, operation tier) S3 request pricing, live-fetched.

    AWS publishes one `Requests-Tier1` SKU covering PUT/COPY/POST/LIST (charged per 1k) and
    one `Requests-Tier2` SKU covering GET/SELECT/HEAD (per 1k), both keyed by region +
    storage class. They're a single call away from each other so we always fetch both at once.
    """

    region: str
    storage_class: str
    price_per_1k_put: float  # Tier1: PUT, COPY, POST, LIST
    price_per_1k_get: float  # Tier2: GET, SELECT, HEAD
    source: str = "aws_price_list_api"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# Cache for request prices, per (region, storage_class). Storage class is part of the
# Price List API key for request SKUs because some pricing varies by class. The cache TTL is
# a day: AWS's published price list changes on the order of months, not hours, and pricing
# drift between calls is not the kind of thing that matters at a daily cadence.
_REQUEST_PRICE_CACHE: dict[tuple[str, str], tuple[S3RequestPriceInfo, datetime]] = {}
_REQUEST_PRICE_CACHE_TTL = timedelta(hours=24)


def get_cached_request_price(region: str, storage_class: str) -> S3RequestPriceInfo | None:
    """Read from the in-process request-price cache. Exposed for tests."""
    entry = _REQUEST_PRICE_CACHE.get((region, storage_class))
    if entry is None:
        return None
    info, cached_at = entry
    if datetime.now(UTC) - cached_at > _REQUEST_PRICE_CACHE_TTL:
        return None
    return info


def _session(credentials: AWSCredentials, *, region: str | None = None) -> Any:
    kwargs: dict[str, Any] = {"region_name": region or credentials.region}
    if credentials.access_key_id and credentials.secret_access_key:
        kwargs["aws_access_key_id"] = credentials.access_key_id
        kwargs["aws_secret_access_key"] = credentials.secret_access_key
    return boto3.Session(**kwargs)


def test_aws_connection(credentials: AWSCredentials) -> AWSConnectionTestResult:
    """Verify credentials work via sts:GetCallerIdentity — free, no extra permissions needed."""
    try:
        client = _session(credentials).client("sts")
        identity = client.get_caller_identity()
        return AWSConnectionTestResult(
            ok=True,
            account_id=identity.get("Account"),
            arn=identity.get("Arn"),
        )
    except Exception as exc:
        return AWSConnectionTestResult(ok=False, error=str(exc))


def fetch_bucket_storage_metrics(
    bucket: str,
    credentials: AWSCredentials,
    *,
    lookback_days: int = 4,
) -> BucketStorageMetrics:
    """Read S3's free, automatic daily storage metrics for a bucket.

    Uses ``get_metric_statistics`` (not ``get_metric_data``) so this stays within CloudWatch's
    free API-request tier. ``lookback_days`` defaults to 4 to comfortably span the ~24h daily
    publish delay and any weekend/holiday gaps, then takes the most recent datapoint found.
    """
    cloudwatch = _session(credentials).client("cloudwatch")
    now = datetime.now(UTC)
    start = now - timedelta(days=lookback_days)

    try:
        size_by_class: dict[str, int] = {}
        for storage_type in _STORAGE_TYPES:
            datapoints = _get_metric_statistics(
                cloudwatch,
                metric_name="BucketSizeBytes",
                dimensions=[
                    {"Name": "BucketName", "Value": bucket},
                    {"Name": "StorageType", "Value": storage_type},
                ],
                start=start,
                end=now,
            )
            if datapoints:
                size_by_class[storage_type] = int(_latest(datapoints)["Average"])

        object_count: int | None = None
        object_datapoints = _get_metric_statistics(
            cloudwatch,
            metric_name="NumberOfObjects",
            dimensions=[
                {"Name": "BucketName", "Value": bucket},
                {"Name": "StorageType", "Value": "AllStorageTypes"},
            ],
            start=start,
            end=now,
        )
        measured_at = None
        if object_datapoints:
            latest = _latest(object_datapoints)
            object_count = int(latest["Average"])
            measured_at = latest["Timestamp"]
        elif size_by_class:
            # Fall back to the most recent storage-metric timestamp if NumberOfObjects had no data.
            measured_at = max(
                (
                    _latest(
                        _get_metric_statistics(
                            cloudwatch,
                            metric_name="BucketSizeBytes",
                            dimensions=[
                                {"Name": "BucketName", "Value": bucket},
                                {"Name": "StorageType", "Value": st},
                            ],
                            start=start,
                            end=now,
                        )
                    )["Timestamp"]
                    for st in size_by_class
                ),
                default=None,
            )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to fetch S3 storage metrics for bucket {bucket}: {exc}"
        ) from exc

    return BucketStorageMetrics(
        bucket=bucket,
        region=credentials.region,
        measured_at=measured_at,
        size_bytes_by_storage_class=size_by_class,
        object_count=object_count,
    )


def fetch_s3_price_per_gb(
    region: str,
    credentials: AWSCredentials,
    *,
    storage_class: str = "General Purpose",
) -> S3PriceInfo | None:
    """Look up the real, current S3 storage price for a region via the free Price List API.

    Returns None (rather than raising) on anything unexpected — an unmapped region, a parsing
    surprise in AWS's pricing JSON, or a permissions gap — since this is meant to *supplement*
    Cryo's existing heuristic $/GB estimate, not replace it with something that can hard-fail a
    scan.
    """
    region_name = _REGION_NAMES.get(region)
    if region_name is None:
        return None
    try:
        pricing = _session(credentials, region=PRICING_API_REGION).client("pricing")
        response = pricing.get_products(
            ServiceCode="AmazonS3",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage"},
                {"Type": "TERM_MATCH", "Field": "location", "Value": region_name},
                {"Type": "TERM_MATCH", "Field": "storageClass", "Value": storage_class},
                {"Type": "TERM_MATCH", "Field": "volumeType", "Value": "Standard"},
            ],
            MaxResults=5,
        )
        for raw in response.get("PriceList", []):
            price = _extract_price_per_unit(json.loads(raw))
            if price is not None:
                return S3PriceInfo(
                    region=region, storage_class=storage_class, price_per_gb_month=price
                )
        return None
    except Exception:
        return None


def fetch_s3_request_prices(
    region: str,
    credentials: AWSCredentials,
    *,
    storage_class: str = "General Purpose",
    use_cache: bool = True,
    fallback_price_per_1k_put: float,
    fallback_price_per_1k_get: float,
) -> S3RequestPriceInfo:
    """Look up live S3 request pricing (PUT/COPY/POST/LIST and GET/SELECT/HEAD) for a region.

    Reuses the same Price List API call pattern as ``fetch_s3_price_per_gb`` so a single
    IAM-granted ``pricing:GetProducts`` covers both. Caches the result per
    (region, storage class) for a day (AWS prices change on the order of months, not hours,
    and request-pricing drift between calls is not the kind of signal that matters at a
    daily cadence).

    ``fallback_price_per_1k_put``/``fallback_price_per_1k_get`` are **required, not
    optional** — the design doc (§6.4) is explicit that a missing price must fall back to
    *the account's configured default rate*, never to zero (zero would make that operation
    type look free, which is a worse error than a stale-but-plausible number). This module
    deliberately doesn't import ``cryo.config`` itself to stay a thin AWS-API wrapper, so the
    caller (which has ``Settings``) must supply the defaults explicitly rather than this
    function inventing its own.

    Always returns a usable :class:`S3RequestPriceInfo` — **never ``None``** — so callers get
    exactly one fallback code path to write, not one inside this function and a duplicate one
    in every caller. Check ``.source`` to decide how much to trust the number:

    - ``"aws_price_list_api"`` — both tiers came back live from AWS.
    - ``"aws_price_list_api_partial"`` — one tier came back live, the other used the caller's
      fallback default. This is the "permitted but empty for one SKU" AWS quirk (§6.4): a
      region that exists in the billing console can still have an incomplete Price List entry,
      and this must be visually distinguishable from a fully-live fetch.
    - ``"fallback_default"`` — the API call was denied, raised, or returned zero matching SKUs
      entirely (region unresolvable, credentials lack ``pricing:GetProducts``, or AWS's Price
      List has no entry at all for this region/storage-class). Both tiers use the caller's
      configured defaults. **Logs a single grep-able WARNING** in every case that reaches this
      branch — a silent-empty response is exactly what the whole "fetch live pricing" decision
      exists to prevent, so it must never look identical to a successful fetch anywhere in the
      pipeline.
    """
    cache_key = (region, storage_class)
    if use_cache:
        cached = get_cached_request_price(region, storage_class)
        if cached is not None:
            return cached

    def _fallback(reason: str) -> S3RequestPriceInfo:
        logger.warning(
            "S3 request pricing: falling back to configured defaults for region=%r "
            "storage_class=%r (%s). Cost derived from this will be approximate, not the "
            "live account rate.",
            region,
            storage_class,
            reason,
        )
        info = S3RequestPriceInfo(
            region=region,
            storage_class=storage_class,
            price_per_1k_put=fallback_price_per_1k_put,
            price_per_1k_get=fallback_price_per_1k_get,
            source="fallback_default",
        )
        if use_cache:
            _REQUEST_PRICE_CACHE[cache_key] = (info, datetime.now(UTC))
        return info

    region_name = _REGION_NAMES.get(region)
    if region_name is None:
        return _fallback(f"unrecognized region {region!r}")
    try:
        pricing = _session(credentials, region=PRICING_API_REGION).client("pricing")
        response = pricing.get_products(
            ServiceCode="AmazonS3",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "API Request"},
                {"Type": "TERM_MATCH", "Field": "location", "Value": region_name},
                {"Type": "TERM_MATCH", "Field": "storageClass", "Value": storage_class},
            ],
            MaxResults=20,  # both tiers come back in one call
        )
        tier1 = tier2 = None
        for raw in response.get("PriceList", []):
            product = json.loads(raw)
            sku_attrs = product.get("product", {}).get("attributes", {}) or {}
            group = sku_attrs.get("group", "") or sku_attrs.get("groupDescription", "")
            price = _extract_price_per_unit(product)
            if price is None:
                continue
            if "Tier1" in group or "PUT" in group or "POST" in group or "LIST" in group:
                if tier1 is None:
                    tier1 = price
            elif "Tier2" in group or "GET" in group or "SELECT" in group:
                if tier2 is None:
                    tier2 = price
        if tier1 is None and tier2 is None:
            return _fallback(
                "GetProducts granted but returned 0 matching SKUs — AWS Price List "
                "incomplete for this region/storage-class"
            )
        # Partial: one tier returned, the other didn't. Still useful, but call it out -- and
        # fall back to the caller's configured default for the missing tier, never to zero
        # (see the docstring: zero would make that operation type look free, not approximate).
        source = "aws_price_list_api"
        if tier1 is None or tier2 is None:
            logger.warning(
                "S3 request pricing: GetProducts returned only one tier for region=%r "
                "storage_class=%r (tier1=%s, tier2=%s). Falling back to the configured "
                "default for the missing tier.",
                region,
                storage_class,
                tier1,
                tier2,
            )
            source = "aws_price_list_api_partial"
            if tier1 is None:
                tier1 = fallback_price_per_1k_put
            if tier2 is None:
                tier2 = fallback_price_per_1k_get
        info = S3RequestPriceInfo(
            region=region,
            storage_class=storage_class,
            price_per_1k_put=tier1,
            price_per_1k_get=tier2,
            source=source,
        )
        if use_cache:
            _REQUEST_PRICE_CACHE[cache_key] = (info, datetime.now(UTC))
        return info
    except Exception as exc:
        return _fallback(f"GetProducts raised: {exc}")


def _extract_price_per_unit(product: dict[str, Any]) -> float | None:
    terms = product.get("terms", {}).get("OnDemand", {})
    for term in terms.values():
        for dimension in term.get("priceDimensions", {}).values():
            price_str = dimension.get("pricePerUnit", {}).get("USD")
            if price_str:
                try:
                    return float(price_str)
                except ValueError:
                    continue
    return None


def _get_metric_statistics(
    cloudwatch: Any,
    *,
    metric_name: str,
    dimensions: list[dict[str, str]],
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    response = cloudwatch.get_metric_statistics(
        Namespace="AWS/S3",
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=start,
        EndTime=end,
        Period=86400,  # daily — matches how often S3 actually publishes these
        Statistics=["Average"],
    )
    return list(response.get("Datapoints", []))


def _latest(datapoints: list[dict[str, Any]]) -> dict[str, Any]:
    return max(datapoints, key=lambda point: point["Timestamp"])
