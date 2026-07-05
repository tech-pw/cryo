"""Accurate S3 cost calculations based on AWS pricing (2026).

Pricing data sourced from: https://aws.amazon.com/s3/pricing/
Region: us-east-1 (default)

Storage Classes:
- S3 Standard: $0.023/GB/month
- S3 Standard-IA: $0.0125/GB/month
- S3 One Zone-IA: $0.01/GB/month
- S3 Glacier Instant Retrieval: $0.004/GB/month
- S3 Glacier Flexible Retrieval: $0.0036/GB/month
- S3 Glacier Deep Archive: $0.00099/GB/month
- S3 Intelligent-Tiering: $0.023/GB/month (first 50TB) + monitoring fees

Request Pricing (per 1000 requests):
- PUT/COPY/POST/LIST: $0.005
- GET/SELECT: $0.0004
- DELETE/CANCEL: Free

Data Transfer:
- Transfer IN: Free
- Transfer OUT to Internet: $0.09/GB (first 10TB/month)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class S3StorageClass(StrEnum):
    """S3 storage classes."""

    STANDARD = "STANDARD"
    STANDARD_IA = "STANDARD_IA"
    ONEZONE_IA = "ONEZONE_IA"
    GLACIER_IR = "GLACIER_IR"
    GLACIER = "GLACIER"
    DEEP_ARCHIVE = "DEEP_ARCHIVE"
    INTELLIGENT_TIERING = "INTELLIGENT_TIERING"


@dataclass(frozen=True)
class S3PricingModel:
    """S3 pricing model for a specific region and storage class."""

    storage_class: S3StorageClass
    region: str

    # Storage pricing ($/GB/month)
    storage_cost_per_gb_month: float

    # Request pricing ($/1000 requests)
    put_request_cost_per_1k: float = 0.005
    get_request_cost_per_1k: float = 0.0004

    # Data transfer pricing ($/GB)
    transfer_out_cost_per_gb: float = 0.09

    # Monitoring fees (Intelligent-Tiering only, $/1000 objects/month)
    monitoring_cost_per_1k_objects: float = 0.0025


# Standard S3 pricing for us-east-1 (North Virginia)
S3_PRICING_US_EAST_1: dict[S3StorageClass, S3PricingModel] = {
    S3StorageClass.STANDARD: S3PricingModel(
        storage_class=S3StorageClass.STANDARD,
        region="us-east-1",
        storage_cost_per_gb_month=0.023,
    ),
    S3StorageClass.STANDARD_IA: S3PricingModel(
        storage_class=S3StorageClass.STANDARD_IA,
        region="us-east-1",
        storage_cost_per_gb_month=0.0125,
        put_request_cost_per_1k=0.01,  # Higher than Standard
        get_request_cost_per_1k=0.001,  # Higher than Standard
    ),
    S3StorageClass.ONEZONE_IA: S3PricingModel(
        storage_class=S3StorageClass.ONEZONE_IA,
        region="us-east-1",
        storage_cost_per_gb_month=0.01,
        put_request_cost_per_1k=0.01,
        get_request_cost_per_1k=0.001,
    ),
    S3StorageClass.GLACIER_IR: S3PricingModel(
        storage_class=S3StorageClass.GLACIER_IR,
        region="us-east-1",
        storage_cost_per_gb_month=0.004,
        put_request_cost_per_1k=0.02,
        get_request_cost_per_1k=0.01,
    ),
    S3StorageClass.GLACIER: S3PricingModel(
        storage_class=S3StorageClass.GLACIER,
        region="us-east-1",
        storage_cost_per_gb_month=0.0036,
        put_request_cost_per_1k=0.03,
        get_request_cost_per_1k=0.0004,  # Plus retrieval fees
    ),
    S3StorageClass.DEEP_ARCHIVE: S3PricingModel(
        storage_class=S3StorageClass.DEEP_ARCHIVE,
        region="us-east-1",
        storage_cost_per_gb_month=0.00099,
        put_request_cost_per_1k=0.05,
        get_request_cost_per_1k=0.0004,  # Plus retrieval fees
    ),
    S3StorageClass.INTELLIGENT_TIERING: S3PricingModel(
        storage_class=S3StorageClass.INTELLIGENT_TIERING,
        region="us-east-1",
        storage_cost_per_gb_month=0.023,  # Same as Standard for frequent access tier
        monitoring_cost_per_1k_objects=0.0025,
    ),
}


def calculate_monthly_storage_cost(
    size_gb: float,
    storage_class: S3StorageClass = S3StorageClass.STANDARD,
    region: str = "us-east-1",
) -> float:
    """Calculate monthly storage cost.

    Args:
        size_gb: Storage size in GB
        storage_class: S3 storage class
        region: AWS region

    Returns:
        Monthly cost in USD
    """
    pricing = S3_PRICING_US_EAST_1.get(storage_class)
    if not pricing:
        # Fallback to Standard pricing
        pricing = S3_PRICING_US_EAST_1[S3StorageClass.STANDARD]

    return size_gb * pricing.storage_cost_per_gb_month


def calculate_request_cost(
    get_requests: int = 0,
    put_requests: int = 0,
    storage_class: S3StorageClass = S3StorageClass.STANDARD,
) -> float:
    """Calculate request cost.

    Args:
        get_requests: Number of GET requests
        put_requests: Number of PUT requests
        storage_class: S3 storage class

    Returns:
        Total request cost in USD
    """
    pricing = S3_PRICING_US_EAST_1.get(storage_class)
    if not pricing:
        pricing = S3_PRICING_US_EAST_1[S3StorageClass.STANDARD]

    get_cost = (get_requests / 1000) * pricing.get_request_cost_per_1k
    put_cost = (put_requests / 1000) * pricing.put_request_cost_per_1k

    return get_cost + put_cost


def calculate_intelligent_tiering_cost(
    size_gb: float,
    object_count: int,
    infrequent_access_pct: float = 0.7,
) -> float:
    """Calculate Intelligent-Tiering cost with automatic tiering.

    Args:
        size_gb: Total storage size in GB
        object_count: Number of objects
        infrequent_access_pct: Percentage of data in infrequent access tier (0-1)

    Returns:
        Monthly cost in USD including monitoring fees
    """
    pricing = S3_PRICING_US_EAST_1[S3StorageClass.INTELLIGENT_TIERING]

    # Storage cost: frequent + infrequent tiers
    frequent_size_gb = size_gb * (1 - infrequent_access_pct)
    infrequent_size_gb = size_gb * infrequent_access_pct

    frequent_cost = frequent_size_gb * 0.023  # Same as Standard
    infrequent_cost = infrequent_size_gb * 0.0125  # Same as Standard-IA

    # Monitoring cost
    monitoring_cost = (object_count / 1000) * pricing.monitoring_cost_per_1k_objects

    return frequent_cost + infrequent_cost + monitoring_cost


def calculate_savings_from_compaction(
    current_file_count: int,
    target_file_count: int,
    avg_file_size_kb: float,
    queries_per_day: float,
    storage_class: S3StorageClass = S3StorageClass.STANDARD,
) -> float:
    """Calculate projected monthly savings from compaction.

    Savings come from:
    1. Reduced LIST API calls (fewer files to list)
    2. Reduced GET requests (fewer files to read per query)
    3. Reduced monitoring fees (Intelligent-Tiering only)

    Args:
        current_file_count: Current number of files
        target_file_count: Expected file count after compaction
        avg_file_size_kb: Average file size in KB
        queries_per_day: Average queries per day
        storage_class: S3 storage class

    Returns:
        Projected monthly savings in USD
    """
    pricing = S3_PRICING_US_EAST_1.get(storage_class, S3_PRICING_US_EAST_1[S3StorageClass.STANDARD])

    # Estimate LIST requests (assume 1 LIST per query to scan metadata)
    list_requests_before = queries_per_day * 30 * (current_file_count / 1000)
    list_requests_after = queries_per_day * 30 * (target_file_count / 1000)
    list_savings = (
        (list_requests_before - list_requests_after) * pricing.put_request_cost_per_1k / 1000
    )

    # Estimate GET requests (assume each query reads files)
    # Queries read fewer files after compaction (fewer small files)
    files_per_query_before = min(current_file_count, 100)  # Assume query scans up to 100 files
    files_per_query_after = min(target_file_count, 100)

    get_requests_before = queries_per_day * 30 * files_per_query_before
    get_requests_after = queries_per_day * 30 * files_per_query_after
    get_savings = (
        (get_requests_before - get_requests_after) * pricing.get_request_cost_per_1k / 1000
    )

    # Monitoring savings (Intelligent-Tiering only)
    monitoring_savings = 0.0
    if storage_class == S3StorageClass.INTELLIGENT_TIERING:
        monitoring_savings = (
            (current_file_count - target_file_count) / 1000
        ) * pricing.monitoring_cost_per_1k_objects

    total_savings = list_savings + get_savings + monitoring_savings

    return max(total_savings, 0.0)


def estimate_rewrite_cost(
    size_gb: float,
    storage_class: S3StorageClass = S3StorageClass.STANDARD,
    engine_cost_per_gb_hour: float = 0.10,  # Trino/Spark compute cost
    estimated_duration_hours: float = 1.0,
) -> float:
    """Estimate one-time cost of compaction/rewrite operation.

    Args:
        size_gb: Amount of data to rewrite
        storage_class: S3 storage class
        engine_cost_per_gb_hour: Compute cost per GB per hour
        estimated_duration_hours: Estimated operation duration

    Returns:
        Estimated cost in USD
    """
    pricing = S3_PRICING_US_EAST_1.get(storage_class, S3_PRICING_US_EAST_1[S3StorageClass.STANDARD])

    # Read cost (GET all input files)
    read_requests = size_gb * 1000  # Assume 1 MB average file size
    read_cost = (read_requests / 1000) * pricing.get_request_cost_per_1k

    # Write cost (PUT all output files)
    write_requests = size_gb * 10  # Assume 100 MB output files
    write_cost = (write_requests / 1000) * pricing.put_request_cost_per_1k

    # Compute cost
    compute_cost = size_gb * engine_cost_per_gb_hour * estimated_duration_hours

    return read_cost + write_cost + compute_cost
