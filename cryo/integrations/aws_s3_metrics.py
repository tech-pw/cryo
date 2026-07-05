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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3

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
    except Exception as exc:
        raise RuntimeError(
            f"Failed to fetch S3 storage metrics for bucket {bucket}: {exc}"
        ) from exc

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
