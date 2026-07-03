from __future__ import annotations

from dataclasses import dataclass

MB = 1024 * 1024
GB = 1024 * MB
S3_COST_PER_GB_MONTH = 0.023


@dataclass(frozen=True)
class StorageCostEstimate:
    monthly_s3_cost_usd: float
    snapshot_overhead_gb: float


def estimate_monthly_s3_cost_usd(total_size_bytes: int, snapshot_overhead_bytes: int = 0) -> float:
    total_gb = (total_size_bytes + snapshot_overhead_bytes) / GB
    return round(total_gb * S3_COST_PER_GB_MONTH, 2)


def estimate_storage_cost(
    *,
    total_size_bytes: int,
    total_delete_bytes: int = 0,
    manifest_total_bytes: int = 0,
    total_snapshots: int = 0,
) -> StorageCostEstimate:
    snapshot_overhead_bytes = 0
    if total_snapshots > 10:
        snapshot_overhead_bytes = int(total_size_bytes * 0.3 * min(total_snapshots / 100, 1.0))
    total_storage_bytes = (
        total_size_bytes + total_delete_bytes + manifest_total_bytes + snapshot_overhead_bytes
    )
    return StorageCostEstimate(
        monthly_s3_cost_usd=estimate_monthly_s3_cost_usd(total_storage_bytes),
        snapshot_overhead_gb=round(snapshot_overhead_bytes / GB, 3),
    )
