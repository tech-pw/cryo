"""Iceberg table property recommendations derived from scanned TableStats.

Tier 1 — metadata-only properties applied via PyIceberg ``set_properties``.
Tier 2 — recommendations that link into existing layout / bloom / run flows.
Tier 3 — read-only or caution banners (gc kill-switch, history expiry, format upgrade).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import LayoutRecommendation, Table, TableStats
from cryo.optimizer.layout_analyzer import analyze_table_layout

RecommendationTier = Literal["tier1", "tier2", "tier3"]
RecommendationAction = Literal[
    "apply",
    "link_layout",
    "link_bloom",
    "link_run",
    "read_only",
    "banner",
    "informational",
]

TIER1_APPLYABLE_KEYS = frozenset(
    {
        "write.target-file-size-bytes",
        "write.distribution-mode",
        "write.metadata.delete-after-commit.enabled",
        "write.metadata.previous-versions-max",
        "commit.retry.num-retries",
        "commit.retry.min-wait-ms",
        "commit.retry.max-wait-ms",
        "write.delete-mode",
        "write.update-mode",
        "write.merge-mode",
    }
)

BLOOM_FILTER_PROPERTY_PREFIX = "write.parquet.bloom-filter-enabled.column."

RISKY_APPLYABLE_KEYS = frozenset({"format-version"})

_DEFAULT_TARGET_BYTES = 256 * 1024 * 1024
_LARGE_TARGET_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class PropertyRecommendation:
    property_key: str
    current_value: str | None
    recommended_value: str | None
    reason: str
    tier: RecommendationTier
    action: RecommendationAction
    layout_recommendation_id: uuid.UUID | None = None
    bloom_filter_column: str | None = None
    needs_confirmation: bool = False


def _props_from_stats(stats: TableStats | None, raw: dict[str, Any]) -> dict[str, str]:
    iceberg = raw.get("iceberg_properties")
    if isinstance(iceberg, dict):
        return {str(k): str(v) for k, v in iceberg.items()}
    out: dict[str, str] = {}
    if stats is not None:
        if stats.target_file_size_bytes is not None:
            out["write.target-file-size-bytes"] = str(stats.target_file_size_bytes)
        if stats.write_distribution_mode:
            out["write.distribution-mode"] = stats.write_distribution_mode
        if stats.has_sort_order:
            out["write.sort-order"] = "set"
    return out


def _stats_dict(stats: TableStats | None, raw: dict[str, Any]) -> dict[str, Any]:
    if stats is None:
        return dict(raw)
    return {
        "total_files": stats.total_files,
        "small_files_count": stats.small_files_count,
        "total_size_gb": stats.total_size_gb,
        "avg_file_size_mb": stats.avg_file_size_mb,
        "partition_skew_ratio": stats.partition_skew_ratio,
        "total_partitions": stats.total_partitions,
        "manifest_count": stats.manifest_count,
        "total_snapshots": stats.total_snapshots,
        "delete_file_pct": stats.delete_file_pct,
        "total_delete_files": stats.total_delete_files,
        "equality_delete_files": int(raw.get("equality_delete_files") or 0),
        "position_delete_files": int(raw.get("position_delete_files") or 0),
        "write_distribution_mode": stats.write_distribution_mode,
        "has_sort_order": stats.has_sort_order,
        "target_file_size_bytes": stats.target_file_size_bytes,
        "commits_per_day": raw.get("commits_per_day"),
        "is_streaming": raw.get("is_streaming"),
        "v3_upgrade_candidate": raw.get("v3_upgrade_candidate"),
        "format_version": raw.get("format_version"),
        "gc_enabled": raw.get("gc_enabled"),
    }


def _format_bytes_human(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024 * 1024:
        return f"{num_bytes / (1024**3):.0f}GB"
    return f"{num_bytes / (1024 * 1024):.0f}MB"


def _recommend_target_file_size(stats: dict[str, Any]) -> tuple[int, str]:
    avg_mb = float(stats.get("avg_file_size_mb") or 0)
    small_files = int(stats.get("small_files_count") or 0)
    total_files = int(stats.get("total_files") or 0)
    target = _LARGE_TARGET_BYTES if avg_mb >= 100 and small_files > 1000 else _DEFAULT_TARGET_BYTES
    reason = (
        f"Target {_format_bytes_human(target)} — average file size is {avg_mb:.0f}MB "
        f"across {total_files:,} files ({small_files:,} small files)"
    )
    return target, reason


def compute_property_recommendations(
    session: Session,
    table_id: uuid.UUID,
    *,
    include_bloom_filters: bool = True,
) -> list[PropertyRecommendation]:
    """``include_bloom_filters=False`` skips the layout analyzer, which loads the table from
    the live catalog -- callers that sweep many tables on a request path must pass it."""
    table = session.get(Table, table_id)
    if table is None:
        return []
    stats = session.get(TableStats, table_id)
    raw = dict((stats.raw or {}) if stats else {})
    props = _props_from_stats(stats, raw)
    stats_dict = _stats_dict(stats, raw)
    recommendations: list[PropertyRecommendation] = []

    gc_enabled = stats_dict.get("gc_enabled")
    if gc_enabled is False or str(gc_enabled).lower() == "false":
        recommendations.append(
            PropertyRecommendation(
                property_key="gc.enabled",
                current_value="false",
                recommended_value=None,
                reason=(
                    "Maintenance operations are disabled at the table level — "
                    "expire_snapshots and remove_orphan_files will not run on this table."
                ),
                tier="tier3",
                action="banner",
            )
        )

    current_target = props.get("write.target-file-size-bytes")
    recommended_target, target_reason = _recommend_target_file_size(stats_dict)
    if current_target is None or int(current_target) != recommended_target:
        recommendations.append(
            PropertyRecommendation(
                property_key="write.target-file-size-bytes",
                current_value=current_target,
                recommended_value=str(recommended_target),
                reason=target_reason,
                tier="tier1",
                action="apply",
            )
        )

    dist_mode = props.get("write.distribution-mode")
    skew = float(stats_dict.get("partition_skew_ratio") or 0)
    partitioned = int(stats_dict.get("total_partitions") or 0) > 1
    if partitioned and (not dist_mode or dist_mode == "none"):
        recommendations.append(
            PropertyRecommendation(
                property_key="write.distribution-mode",
                current_value=dist_mode or "none",
                recommended_value="hash",
                reason=(
                    f"Partition skew ratio is {skew:.1f}x — hash distribution spreads "
                    "writes across buckets for partitioned tables."
                ),
                tier="tier1",
                action="apply",
            )
        )

    manifest_count = int(stats_dict.get("manifest_count") or 0)
    snapshot_count = int(stats_dict.get("total_snapshots") or 0)
    delete_after = props.get("write.metadata.delete-after-commit.enabled")
    prev_max = props.get("write.metadata.previous-versions-max")
    if manifest_count >= 100 or snapshot_count >= 50:
        if delete_after is None or str(delete_after).lower() != "true":
            recommendations.append(
                PropertyRecommendation(
                    property_key="write.metadata.delete-after-commit.enabled",
                    current_value=delete_after,
                    recommended_value="true",
                    reason=(
                        f"{manifest_count} manifest files and {snapshot_count} snapshots — "
                        "enable metadata cleanup after each commit."
                    ),
                    tier="tier1",
                    action="apply",
                )
            )
        if prev_max is None or int(prev_max) > 20:
            recommendations.append(
                PropertyRecommendation(
                    property_key="write.metadata.previous-versions-max",
                    current_value=prev_max,
                    recommended_value="10",
                    reason=(
                        f"Cap retained metadata versions — {manifest_count} manifests "
                        "suggest metadata file buildup."
                    ),
                    tier="tier1",
                    action="apply",
                )
            )

    commits_per_day = float(stats_dict.get("commits_per_day") or 0)
    is_streaming = bool(stats_dict.get("is_streaming"))
    if is_streaming or commits_per_day >= 24:
        retry_num = props.get("commit.retry.num-retries")
        if retry_num is None or int(retry_num) < 10:
            recommendations.append(
                PropertyRecommendation(
                    property_key="commit.retry.num-retries",
                    current_value=retry_num,
                    recommended_value="10",
                    reason=(
                        f"High commit velocity ({commits_per_day:.1f} commits/day) — "
                        "increase commit retry budget for concurrent writers."
                    ),
                    tier="tier1",
                    action="apply",
                )
            )
        min_wait = props.get("commit.retry.min-wait-ms")
        if min_wait is None or int(min_wait) < 100:
            recommendations.append(
                PropertyRecommendation(
                    property_key="commit.retry.min-wait-ms",
                    current_value=min_wait,
                    recommended_value="100",
                    reason="Pair with higher retry count for streaming write contention.",
                    tier="tier1",
                    action="apply",
                )
            )
        max_wait = props.get("commit.retry.max-wait-ms")
        if max_wait is None or int(max_wait) < 60000:
            recommendations.append(
                PropertyRecommendation(
                    property_key="commit.retry.max-wait-ms",
                    current_value=max_wait,
                    recommended_value="60000",
                    reason="Allow up to 60s backoff between commit retries on hot tables.",
                    tier="tier1",
                    action="apply",
                )
            )

    codec = props.get("write.parquet.compression-codec")
    if codec is None:
        recommendations.append(
            PropertyRecommendation(
                property_key="write.parquet.compression-codec",
                current_value=None,
                recommended_value="zstd",
                reason="zstd is the current industry default — good balance of ratio and speed.",
                tier="tier1",
                action="informational",
            )
        )
    elif str(codec).lower() != "zstd":
        recommendations.append(
            PropertyRecommendation(
                property_key="write.parquet.compression-codec",
                current_value=codec,
                recommended_value="zstd",
                reason=(
                    f"Current codec {codec} — zstd typically improves compression "
                    "with similar scan cost."
                ),
                tier="tier1",
                action="informational",
            )
        )

    layout_rec = session.scalar(
        select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
    )
    has_sort = bool(stats_dict.get("has_sort_order")) or props.get("write.sort-order")
    layout_strategies = {"sort", "zorder"}
    if (
        layout_rec
        and layout_rec.status in {"pending", "approved"}
        and layout_rec.recommended_strategy in layout_strategies
    ):
        recommendations.append(
            PropertyRecommendation(
                property_key="write.sort-order",
                current_value=props.get("write.sort-order") or ("set" if has_sort else None),
                recommended_value=layout_rec.recommended_strategy,
                reason=(
                    f"Layout analyzer recommends {layout_rec.recommended_strategy} on "
                    f"{len(layout_rec.recommended_columns or [])} column(s) for better pruning."
                ),
                tier="tier2",
                action="link_layout",
                layout_recommendation_id=layout_rec.id,
            )
        )
    elif not has_sort:
        recommendations.append(
            PropertyRecommendation(
                property_key="write.sort-order",
                current_value=None,
                recommended_value="sort or z-order",
                reason=(
                    "No sort order configured — clustering improves file pruning "
                    "for filtered queries."
                ),
                tier="tier2",
                action="link_layout",
                layout_recommendation_id=layout_rec.id if layout_rec else None,
            )
        )

    delete_pct = float(stats_dict.get("delete_file_pct") or 0)
    eq_deletes = int(stats_dict.get("equality_delete_files") or 0)
    merge_mode = props.get("write.merge-mode") or props.get("write.update-mode")
    recommended_mode = "merge-on-read" if delete_pct >= 5 or eq_deletes >= 100 else "copy-on-write"
    if merge_mode is None or str(merge_mode) != recommended_mode:
        recommendations.append(
            PropertyRecommendation(
                property_key="write.merge-mode",
                current_value=merge_mode,
                recommended_value=recommended_mode,
                reason=(
                    f"Delete-file load is {delete_pct:.1f}% ({eq_deletes:,} equality deletes) — "
                    f"{recommended_mode} fits this table's update pattern."
                ),
                tier="tier2",
                action="apply",
            )
        )

    try:
        blooms = (
            analyze_table_layout(session, table_id).bloom_filter_recommendations[:3]
            if include_bloom_filters
            else []
        )
        for bloom in blooms:
            prop_key = f"{BLOOM_FILTER_PROPERTY_PREFIX}{bloom.column_name}"
            if props.get(prop_key) == "true":
                continue
            recommendations.append(
                PropertyRecommendation(
                    property_key=prop_key,
                    current_value=props.get(prop_key),
                    recommended_value="true",
                    reason=bloom.reason,
                    tier="tier2",
                    action="link_bloom",
                    bloom_filter_column=bloom.column_name,
                )
            )
    except Exception:
        pass

    expire_age = props.get("history.expire.max-snapshot-age-ms")
    expire_min = props.get("history.expire.min-snapshots-to-keep")
    if expire_age or expire_min:
        recommendations.append(
            PropertyRecommendation(
                property_key="history.expire.max-snapshot-age-ms",
                current_value=expire_age,
                recommended_value=None,
                reason=(
                    "Native Iceberg auto-expiry is set — reconcile with Cryo's scheduled "
                    "expire_snapshots preset or leave unset so Cryo is the sole retention "
                    "mechanism."
                ),
                tier="tier3",
                action="read_only",
            )
        )

    format_version = stats_dict.get("format_version") or props.get("format-version")
    if stats_dict.get("v3_upgrade_candidate") and str(format_version or "2") != "3":
        recommendations.append(
            PropertyRecommendation(
                property_key="format-version",
                current_value=str(format_version or "2"),
                recommended_value="3",
                reason=(
                    "Eligible for Iceberg v3 (deletion vectors) — verify all readers support "
                    "format version 3 before upgrading."
                ),
                tier="tier3",
                action="apply",
                needs_confirmation=True,
            )
        )

    return recommendations


def count_suboptimal_property_issues(stats: Mapping[str, Any]) -> int:
    """Count property gaps used by health scoring (itemized suboptimal-properties check)."""
    issues = 0
    if stats.get("write_distribution_mode", "none") == "none":
        issues += 1
    if not stats.get("has_sort_order", False):
        issues += 1
    if not stats.get("target_file_size_bytes"):
        issues += 1
    return issues


def apply_table_properties(
    session: Session,
    table_id: uuid.UUID,
    properties: dict[str, str],
    *,
    confirm_risky: bool = False,
) -> dict[str, str]:
    """Persist table properties via PyIceberg. Returns applied key→value map."""
    from cryo.db.models import Source, Table
    from cryo.sources.pyiceberg_catalog import PyIcebergSourceCatalog

    allowed: dict[str, str] = {}
    for key, value in properties.items():
        if key in TIER1_APPLYABLE_KEYS or key.startswith(BLOOM_FILTER_PROPERTY_PREFIX):
            allowed[key] = value
        elif key in RISKY_APPLYABLE_KEYS and confirm_risky:
            allowed[key] = value
    if not allowed:
        raise ValueError("No applyable properties in request")

    table = session.get(Table, table_id)
    if table is None:
        raise ValueError("Table not found")
    source = session.get(Source, table.source_id)
    if source is None:
        raise ValueError("Source not found")

    catalog = PyIcebergSourceCatalog(name=str(source.id), config=source.config)
    iceberg_table = catalog.load_table(table.schema_name, table.table_name)
    with iceberg_table.transaction() as txn:
        txn.set_properties(**allowed)
    return allowed
