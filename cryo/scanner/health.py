from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

HEALTH_THRESHOLDS = {
    "small_file_pct_warn": 20,
    "small_file_pct_crit": 50,
    "total_files_warn": 10_000,
    "total_files_crit": 100_000,
    "snapshot_count_warn": 50,
    "snapshot_count_crit": 200,
    "snapshot_age_days_warn": 7,
    "snapshot_age_days_crit": 30,
    "delete_file_pct_warn": 5,
    "delete_file_pct_crit": 20,
    "delete_file_count_warn": 100,
    "delete_file_count_crit": 1_000,
    "manifest_count_warn": 100,
    "manifest_count_crit": 500,
    "partition_count_warn": 10_000,
    "partition_count_crit": 100_000,
    "partition_skew_ratio_warn": 10,
    "partition_skew_ratio_crit": 50,
    "avg_files_per_partition_warn": 50,
    "avg_files_per_partition_crit": 200,
    "metadata_to_data_ratio_warn": 0.05,
    "metadata_to_data_ratio_crit": 0.15,
}


def compute_health_score(
    stats: Mapping[str, Any],
    health_small_file_count: int,
    *,
    now: datetime | None = None,
) -> tuple[int, str]:
    score = 100
    total_files = int(stats.get("total_files") or 0)
    small_pct = (health_small_file_count / max(total_files, 1)) * 100
    score = _deduct(score, small_pct, "small_file_pct")
    score = _deduct(score, total_files, "total_files")

    delete_pct = float(stats.get("delete_file_pct") or 0)
    delete_files = int(stats.get("total_delete_files") or 0)
    if delete_pct >= HEALTH_THRESHOLDS["delete_file_pct_crit"]:
        score -= 20
    elif delete_files >= HEALTH_THRESHOLDS["delete_file_count_crit"]:
        score -= 20
    elif delete_pct >= HEALTH_THRESHOLDS["delete_file_pct_warn"]:
        score -= 8
    elif delete_files >= HEALTH_THRESHOLDS["delete_file_count_warn"]:
        score -= 8

    score = _deduct(score, int(stats.get("total_snapshots") or 0), "snapshot_count")
    last_commit = stats.get("last_snapshot_committed_at") or stats.get("first_updated_at")
    score = _deduct_snapshot_age(score, last_commit, now=now)
    score = _deduct(score, int(stats.get("manifest_count") or 0), "manifest_count")
    score = _deduct(score, int(stats.get("total_partitions") or 0), "partition_count")
    score = _deduct(score, float(stats.get("partition_skew_ratio") or 0), "partition_skew_ratio")
    score = _deduct(
        score, float(stats.get("avg_files_per_partition") or 0), "avg_files_per_partition"
    )
    score = _deduct(
        score, float(stats.get("metadata_to_data_ratio") or 0), "metadata_to_data_ratio"
    )

    if stats.get("v3_upgrade_candidate"):
        score -= 8

    if stats.get("is_streaming"):
        score -= 8

    props_issues = 0
    if stats.get("write_distribution_mode", "none") == "none":
        props_issues += 1
    if not stats.get("has_sort_order", False):
        props_issues += 1
    if not stats.get("target_file_size_bytes"):
        props_issues += 1
    if props_issues:
        score -= 8

    file_formats = stats.get("file_formats") or []
    if any(str(file_format).upper() not in {"PARQUET", "ORC"} for file_format in file_formats):
        score -= 8

    score = max(0, score)
    return score, _grade(score)


def _deduct(score: int, value: float, key: str) -> int:
    if value >= HEALTH_THRESHOLDS[f"{key}_crit"]:
        return score - 20
    if value >= HEALTH_THRESHOLDS[f"{key}_warn"]:
        return score - 8
    return score


def _deduct_snapshot_age(score: int, first_updated_at: Any, *, now: datetime | None) -> int:
    first_updated = _as_datetime(first_updated_at)
    if first_updated is None:
        return score
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    age_days = (current.astimezone(UTC) - first_updated.astimezone(UTC)).days
    return _deduct(score, age_days, "snapshot_age_days")


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value / 1000 if value > 10_000_000_000 else value, UTC)
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"
