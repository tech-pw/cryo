from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, TypedDict

from cryo.scanner.property_recommendations import count_suboptimal_property_issues

# Must match cryo/planner/planner.py's own `min_small_files` default (the count below which
# the planner decides OPTIMIZE isn't worth scheduling at all -- see planner.py:180). Grading a
# table critical for small-file fragmentation while the planner will never schedule a fix for
# it produces a permanent, unfixable grade: a 3-file table where all 3 are "small" is 100%
# small-file-by-percentage, but merging 3 files isn't worth a compaction job. Found via a real
# fleet-wide before/after (2026-08-05, ): 76% of D-graded tables had fewer total files
# than this floor, and clearing this one deduction alone moved 99.6% of the whole D bucket to
# C or better -- confirming the deduction, not the table, was the actual problem for most of
# them.
MIN_SMALL_FILES_FOR_COMPACTION = 128

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


class HealthDeduction(TypedDict):
    """A single scoring deduction — the sole source of truth for the Health & Diagnosis UI.

    Persisted verbatim into ``TableStats.raw["health_deductions"]`` (see
    ``cryo/scanner/analyze.py``) and rendered as-is by the frontend, so the UI can never drift
    from the score `compute_health_score` actually produced.
    """

    label: str
    deduction: int
    severity: str
    reason: str


def _delete_reason(pct: float, count: int, limit_pct: float, limit_count: float) -> str:
    return (
        f"{pct:.1f}% deletes or {count} count "
        f"(exceeds limit of {limit_pct:.0f}% / {int(limit_count):,})"
    )


def _avg_files_per_partition_reason(value: float, limit: float) -> str:
    return f"Average files/partition is {value:.1f} (exceeds limit of {limit:.0f})"


def _metadata_ratio_reason(value: float, limit: float) -> str:
    return f"Metadata ratio of {value * 100:.1f}% (exceeds limit of {limit * 100:.1f}%)"


def _snapshot_age_reason(value: float, limit: float) -> str:
    return f"Last commit was {int(value)} days ago (exceeds limit of {int(limit)} days)"


def _size_scale(total_size_gb: float) -> float:
    """Scale count-based thresholds for large tables (sqrt of 100 GB units)."""
    return float(max(1.0, ((total_size_gb or 0) / 100.0) ** 0.5))


def _scaled_thresholds(total_size_gb: float) -> dict[str, int | float]:
    scale = _size_scale(total_size_gb)
    scaled: dict[str, int | float] = {}
    for key, value in HEALTH_THRESHOLDS.items():
        if "pct" in key or "ratio" in key or "age" in key:
            scaled[key] = value
        elif isinstance(value, int):
            scaled[key] = int(value * scale)
        else:
            scaled[key] = value * scale
    return scaled


def compute_health_score(
    stats: Mapping[str, Any],
    health_small_file_count: int,
    *,
    now: datetime | None = None,
) -> tuple[int, str, list[HealthDeduction]]:
    thresholds = _scaled_thresholds(float(stats.get("total_size_gb") or 0))
    deductions: list[HealthDeduction] = []
    score = 100
    total_files = int(stats.get("total_files") or 0)
    if health_small_file_count >= MIN_SMALL_FILES_FOR_COMPACTION:
        small_pct = (health_small_file_count / max(total_files, 1)) * 100
        score = _deduct(
            score,
            deductions,
            small_pct,
            "small_file_pct",
            "Small-file fragmentation",
            thresholds,
            lambda value, limit: f"{value:.1f}% small files (exceeds threshold of {limit:.0f}%)",
        )
    score = _deduct(
        score,
        deductions,
        total_files,
        "total_files",
        "Excessive file count",
        thresholds,
        lambda value, limit: f"{int(value):,} files (exceeds limit of {int(limit):,})",
        label_warn="High file count",
    )

    delete_pct = float(stats.get("delete_file_pct") or 0)
    delete_files = int(stats.get("total_delete_files") or 0)
    delete_pct_crit = thresholds["delete_file_pct_crit"]
    delete_count_crit = thresholds["delete_file_count_crit"]
    delete_pct_warn = thresholds["delete_file_pct_warn"]
    delete_count_warn = thresholds["delete_file_count_warn"]
    if delete_pct >= delete_pct_crit or delete_files >= delete_count_crit:
        score -= 20
        deductions.append(
            {
                "label": "Position delete file buildup",
                "deduction": 20,
                "severity": "critical",
                "reason": _delete_reason(
                    delete_pct, delete_files, delete_pct_crit, delete_count_crit
                ),
            }
        )
    elif delete_pct >= delete_pct_warn or delete_files >= delete_count_warn:
        score -= 8
        deductions.append(
            {
                "label": "Position delete file buildup",
                "deduction": 8,
                "severity": "warning",
                "reason": _delete_reason(
                    delete_pct, delete_files, delete_pct_warn, delete_count_warn
                ),
            }
        )

    equality_files = int(stats.get("equality_delete_files") or 0)
    if equality_files >= 20:
        score -= 20
        deductions.append(
            {
                "label": "Equality delete buildup",
                "deduction": 20,
                "severity": "critical",
                "reason": (
                    f"{equality_files} equality-delete files. "
                    "convert_equality_deletes turns them into position deletes "
                    "without rewriting data files."
                ),
            }
        )
    elif equality_files >= 1:
        score -= 8
        deductions.append(
            {
                "label": "Equality delete buildup",
                "deduction": 8,
                "severity": "warning",
                "reason": (
                    f"{equality_files} equality-delete files. "
                    "convert_equality_deletes turns them into position deletes "
                    "without rewriting data files."
                ),
            }
        )

    score = _deduct(
        score,
        deductions,
        int(stats.get("total_snapshots") or 0),
        "snapshot_count",
        "Snapshot retention buildup",
        thresholds,
        lambda value, limit: f"{int(value)} snapshots (exceeds limit of {int(limit)})",
    )
    last_commit = stats.get("last_snapshot_committed_at") or stats.get("first_updated_at")
    score = _deduct_snapshot_age(score, deductions, last_commit, now=now, thresholds=thresholds)
    score = _deduct(
        score,
        deductions,
        int(stats.get("manifest_count") or 0),
        "manifest_count",
        "Manifest files bloat",
        thresholds,
        lambda value, limit: f"{int(value)} manifests (exceeds limit of {int(limit)})",
    )
    score = _deduct(
        score,
        deductions,
        int(stats.get("total_partitions") or 0),
        "partition_count",
        "Partition count complexity",
        thresholds,
        lambda value, limit: f"{int(value)} partitions (exceeds limit of {int(limit)})",
    )
    score = _deduct(
        score,
        deductions,
        float(stats.get("partition_skew_ratio") or 0),
        "partition_skew_ratio",
        "Partition volume skew",
        thresholds,
        lambda value, limit: f"Skew ratio of {value:.1f}x (exceeds limit of {limit:.0f}x)",
    )
    score = _deduct(
        score,
        deductions,
        float(stats.get("avg_files_per_partition") or 0),
        "avg_files_per_partition",
        "Fragmented partitions",
        thresholds,
        _avg_files_per_partition_reason,
    )
    score = _deduct(
        score,
        deductions,
        float(stats.get("metadata_to_data_ratio") or 0),
        "metadata_to_data_ratio",
        "Metadata ratio overhead",
        thresholds,
        _metadata_ratio_reason,
    )

    if stats.get("v3_upgrade_candidate"):
        score -= 8
        deductions.append(
            {
                "label": "V3 upgrade candidate",
                "deduction": 8,
                "severity": "warning",
                "reason": "Eligible for upgrade to Iceberg V3 format",
            }
        )

    if stats.get("is_streaming"):
        score -= 8
        commits_per_day = float(stats.get("commits_per_day") or 0.0)
        reason = "Table receives streaming writes causing high commit velocity"
        if commits_per_day > 0:
            reason = (
                f"{reason} ({commits_per_day:.1f} commits/day from snapshot-count deltas "
                "across table_stats_history)"
            )
        deductions.append(
            {
                "label": "High velocity streaming",
                "deduction": 8,
                "severity": "warning",
                "reason": reason,
            }
        )

    props_issues = count_suboptimal_property_issues(stats)
    if props_issues:
        score -= 8
        deductions.append(
            {
                "label": "Suboptimal properties",
                "deduction": 8,
                "severity": "warning",
                "reason": (
                    f"{props_issues} alterable property gap(s) — missing sort order, "
                    "distribution mode, or target file size settings"
                ),
            }
        )

    file_formats = stats.get("file_formats") or []
    if any(str(file_format).upper() not in {"PARQUET", "ORC"} for file_format in file_formats):
        score -= 8
        deductions.append(
            {
                "label": "Legacy file formats",
                "deduction": 8,
                "severity": "warning",
                "reason": "Non-Parquet/ORC file formats detected",
            }
        )

    score = max(0, score)
    return score, _grade(score), deductions


def _deduct(
    score: int,
    deductions: list[HealthDeduction],
    value: float,
    key: str,
    label: str,
    thresholds: Mapping[str, int | float],
    reason_fn: Any,
    *,
    label_warn: str | None = None,
) -> int:
    if value >= thresholds[f"{key}_crit"]:
        deductions.append(
            {
                "label": label,
                "deduction": 20,
                "severity": "critical",
                "reason": reason_fn(value, thresholds[f"{key}_crit"]),
            }
        )
        return score - 20
    if value >= thresholds[f"{key}_warn"]:
        deductions.append(
            {
                "label": label_warn or label,
                "deduction": 8,
                "severity": "warning",
                "reason": reason_fn(value, thresholds[f"{key}_warn"]),
            }
        )
        return score - 8
    return score


def _deduct_snapshot_age(
    score: int,
    deductions: list[HealthDeduction],
    first_updated_at: Any,
    *,
    now: datetime | None,
    thresholds: Mapping[str, int | float],
) -> int:
    first_updated = _as_datetime(first_updated_at)
    if first_updated is None:
        return score
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    age_days = (current.astimezone(UTC) - first_updated.astimezone(UTC)).days
    return _deduct(
        score,
        deductions,
        age_days,
        "snapshot_age_days",
        "Stale table commits",
        thresholds,
        _snapshot_age_reason,
    )


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
