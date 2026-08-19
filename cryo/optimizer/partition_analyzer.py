"""Partition evolution recommendations engine.

Analyzes query patterns to recommend partition spec changes
for better query pruning and performance.

Guards against the two classic over/under-partitioning anti-patterns (see
docs/RECOMMENDATIONS.md's Iceberg partitioning research):
- Recommending `identity` partitioning on a high-cardinality column (one partition per
  distinct value) — uses the same name-based cardinality heuristic as
  `cryo/optimizer/layout_analyzer.py`'s Bloom filter recommender, since no real NDV stats
  are available yet (Puffin NDV reading is still a stub).
- Recommending a finer time granularity than the data volume can support — estimates
  resulting average partition size and skips the recommendation if it would fall below the
  commonly-cited 128MB-per-partition floor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.model_utils import qualified_table_name
from cryo.db.models import QueryLog, QueryPattern, Table, TableStats

# Same keyword heuristic used by layout_analyzer.py's Bloom filter recommender.
_LOW_CARDINALITY_HINTS = (
    "date",
    "day",
    "month",
    "year",
    "status",
    "type",
    "flag",
    "region",
    "country",
)
_HIGH_CARDINALITY_HINTS = (
    "id",
    "uuid",
    "key",
    "hash",
    "token",
    "code",
    "ref",
    "guid",
    "sku",
)

# Iceberg partitioning best practice: a partition should hold roughly 128MB-1GB of data
# post-compaction. Recommending a spec that would push average partition size below the
# low end reproduces the over-partitioning anti-pattern (excess small files, bloated
# manifests) even though pruning looks good on paper.
_MIN_HEALTHY_PARTITION_GB = 0.125
_MAX_HEALTHY_PARTITION_GB = 1.0
_TARGET_HEALTHY_PARTITION_GB = 0.5
# Algoscale: avg(bytes scanned) / table size > 10% means the partition key
# misaligns with the workload (docs/ICEBERG_MAINTENANCE_INTELLIGENCE_RFC.md §3).
_SCAN_RATIO_THRESHOLD = 0.10
_SCAN_RATIO_MIN_FILTER_PCT = 20.0
_SCAN_RATIO_MIN_QUERIES = 10
_BUCKET_TRANSFORM_RE = re.compile(
    r"bucket\s*[\[(](\d+)[\])]",
    re.IGNORECASE,
)

_GRANULARITY_ORDER = ("year", "month", "day")
# Approximate multiplier in partition *count* when moving from a coarser to a finer
# time transform — a rough calendar estimate used only to size-check a recommendation,
# not an exact computation.
_GRANULARITY_FACTOR = {
    ("year", "month"): 12,
    ("year", "day"): 365,
    ("month", "day"): 30,
}


@dataclass
class PartitionEvolutionRecommendation:
    """Recommendation for changing a table's partition spec."""

    table_id: str
    table_name: str
    current_partition_spec: str
    recommended_partition_spec: str
    reason: str
    confidence: float  # 0.0 to 1.0
    estimated_pruning_improvement_pct: float
    query_patterns_analyzed: int
    # "high" / "low" / "unknown" — name-based heuristic, not a measured NDV.
    cardinality_estimate: str = "unknown"
    warnings: list[str] = field(default_factory=list)
    # Algoscale scan-ratio (avg QueryLog.bytes_read / table bytes), when computable.
    scan_ratio: float | None = None
    # Dominant filter column's QueryPattern.avg_selectivity (0–1), if present.
    avg_selectivity: float | None = None
    # Why this rec fired: filter_frequency | scan_ratio | granularity | bucket_sizing
    trigger: str = "filter_frequency"


def cardinality_hint(column_name: str) -> str:
    """Best-effort cardinality guess from column name alone — "high", "low", or "unknown"."""
    col_lower = column_name.lower()
    if any(kw in col_lower for kw in _HIGH_CARDINALITY_HINTS):
        return "high"
    if any(kw in col_lower for kw in _LOW_CARDINALITY_HINTS):
        return "low"
    return "unknown"


def bucket_count_for(total_size_gb: float) -> int:
    """Pick a bucket count targeting ~_TARGET_HEALTHY_PARTITION_GB per bucket."""
    if total_size_gb <= 0:
        return 16
    estimated = max(4, round(total_size_gb / _TARGET_HEALTHY_PARTITION_GB))
    power = 1
    while power < estimated:
        power *= 2
    return min(2048, power)


def _bucket_count_for(total_size_gb: float) -> int:
    """Private alias — Feature 4 and Feature 1 share one formula."""
    return bucket_count_for(total_size_gb)


def parse_bucket_n(transform: str) -> int | None:
    """Extract N from Iceberg ``bucket[64]`` / ``bucket(64)`` transform text."""
    match = _BUCKET_TRANSFORM_RE.search(transform or "")
    if not match:
        return None
    return int(match.group(1))


def bucket_fields(partition_spec: object) -> list[tuple[str, int, str]]:
    """Return ``(column_name, N, original_transform_text)`` for each bucket field."""
    if not isinstance(partition_spec, list):
        return []
    found: list[tuple[str, int, str]] = []
    for entry in partition_spec:
        if not isinstance(entry, dict):
            continue
        transform_raw = str(entry.get("transform", ""))
        n = parse_bucket_n(transform_raw)
        if n is None or n <= 0:
            continue
        found.append((str(entry.get("name", "")), n, transform_raw))
    return found


def table_scan_ratio(
    session: Session,
    table_id: UUID | str,
    total_size_gb: float,
) -> tuple[float | None, int]:
    """Avg ``QueryLog.bytes_read`` / table size. ``(None, 0)`` when not computable."""
    if total_size_gb <= 0:
        return None, 0
    tid = table_id if isinstance(table_id, UUID) else UUID(str(table_id))
    row = session.execute(
        select(func.avg(QueryLog.bytes_read), func.count(QueryLog.id)).where(
            QueryLog.table_id == tid,
            QueryLog.bytes_read.is_not(None),
            QueryLog.bytes_read > 0,
        )
    ).one()
    avg_bytes, n = row[0], int(row[1] or 0)
    if avg_bytes is None or n < _SCAN_RATIO_MIN_QUERIES:
        return None, n
    table_bytes = total_size_gb * (1024**3)
    if table_bytes <= 0:
        return None, n
    return float(avg_bytes) / table_bytes, n


def analyze_bucket_sizing(
    *,
    table_id: str,
    table_name: str,
    current_spec: str,
    raw_partition_spec: object,
    total_size_gb: float,
    query_patterns_analyzed: int = 0,
    scan_ratio: float | None = None,
) -> PartitionEvolutionRecommendation | None:
    """Re-check an existing ``bucket(col, N)`` against current volume (RFC Feature 4)."""
    fields = bucket_fields(raw_partition_spec)
    if not fields or total_size_gb <= 0:
        return None
    column_name, current_n, transform_raw = fields[0]
    recommended_n = bucket_count_for(total_size_gb)
    avg_gb = total_size_gb / current_n
    too_coarse = recommended_n >= current_n * 2 or avg_gb > _MAX_HEALTHY_PARTITION_GB
    too_fine = recommended_n * 2 <= current_n or avg_gb < _MIN_HEALTHY_PARTITION_GB
    if not too_coarse and not too_fine:
        return None
    if recommended_n == current_n:
        return None
    direction = "too coarse (under-partitioned)" if too_coarse else "too fine (over-partitioned)"
    new_transform = re.sub(
        r"\d+",
        str(recommended_n),
        transform_raw,
        count=1,
    )
    recommended_spec = current_spec.replace(transform_raw, new_transform)
    if recommended_spec == current_spec:
        recommended_spec = f"{column_name} (bucket[{recommended_n}])"
    return PartitionEvolutionRecommendation(
        table_id=table_id,
        table_name=table_name,
        current_partition_spec=current_spec,
        recommended_partition_spec=recommended_spec,
        reason=(
            f"bucket({column_name}, {current_n}) is {direction}: "
            f"~{avg_gb * 1024:.0f}MB per bucket "
            f"(target ~{_TARGET_HEALTHY_PARTITION_GB * 1024:.0f}MB). "
            f"Recommend N={recommended_n}. Changing N only affects future writes unless the "
            "table is rewritten."
        ),
        confidence=0.8,
        estimated_pruning_improvement_pct=40.0 if too_coarse else 0.0,
        query_patterns_analyzed=query_patterns_analyzed,
        cardinality_estimate="high",
        warnings=[
            "Partition-spec evolution needs a human-reviewed rewrite to apply to existing data."
        ],
        scan_ratio=scan_ratio,
        trigger="bucket_sizing",
    )


def _time_partition_field(partition_spec: object) -> tuple[str, str, str] | None:
    """Return (column_name, lowercased transform, original transform text) for the first
    year/month/day field in a partition spec — the original text is kept so the recommended
    spec string can substitute it back in with matching case."""
    if not isinstance(partition_spec, list):
        return None
    for entry in partition_spec:
        if not isinstance(entry, dict):
            continue
        transform_raw = str(entry.get("transform", ""))
        transform = transform_raw.lower()
        if transform in _GRANULARITY_ORDER:
            return str(entry.get("name", "")), transform, transform_raw
    return None


def _finer_transforms(current: str) -> list[str]:
    if current not in _GRANULARITY_ORDER:
        return []
    idx = _GRANULARITY_ORDER.index(current)
    return list(_GRANULARITY_ORDER[idx + 1 :])


def _estimated_avg_partition_gb_after_switch(
    total_size_gb: float,
    total_partitions: int | None,
    current: str,
    candidate: str,
) -> float | None:
    factor = _GRANULARITY_FACTOR.get((current, candidate))
    if not factor or not total_partitions or total_partitions <= 0 or total_size_gb <= 0:
        return None
    estimated_new_partitions = total_partitions * factor
    if estimated_new_partitions <= 0:
        return None
    return total_size_gb / estimated_new_partitions


def analyze_partition_evolution(
    session: Session,
    table_id: UUID | str,
    min_query_threshold: int = 100,
) -> PartitionEvolutionRecommendation | None:
    """
    Analyze if a table would benefit from a different partition spec.

    Args:
        session: Database session
        table_id: Table UUID
        min_query_threshold: Minimum queries needed for recommendation

    Returns:
        Partition evolution recommendation or None if no change needed
    """
    tid = table_id if isinstance(table_id, UUID) else UUID(str(table_id))
    table = session.get(Table, tid)
    if not table:
        raise ValueError(f"Table {table_id} not found")

    stats = session.get(TableStats, tid)
    if not stats:
        return None

    table_name = qualified_table_name(table)

    raw_partition_spec = (
        stats.raw.get("partition_spec", []) if stats.raw and isinstance(stats.raw, dict) else []
    )
    current_spec = "unpartitioned"
    if raw_partition_spec and isinstance(raw_partition_spec, list):
        current_spec = ", ".join(
            f"{p.get('name', '')} ({p.get('transform', '')})" for p in raw_partition_spec
        )

    total_size_gb = float(stats.total_size_gb or 0.0)
    scan_ratio, scan_query_n = table_scan_ratio(session, tid, total_size_gb)

    def _bucket_rec(*, patterns_n: int) -> PartitionEvolutionRecommendation | None:
        return analyze_bucket_sizing(
            table_id=str(tid),
            table_name=table_name,
            current_spec=current_spec,
            raw_partition_spec=raw_partition_spec,
            total_size_gb=total_size_gb,
            query_patterns_analyzed=patterns_n,
            scan_ratio=scan_ratio,
        )

    pattern_stmt = (
        select(QueryPattern)
        .where(QueryPattern.table_id == tid)
        .where(QueryPattern.filter_count >= 10)  # Only consider frequent filter patterns
    )
    patterns = session.execute(pattern_stmt).scalars().all()

    if len(patterns) < min_query_threshold:
        return _bucket_rec(patterns_n=len(patterns))

    filter_column_counts: dict[str, int] = {}
    total_queries = 0
    selectivity_by_column: dict[str, float] = {}

    for pattern in patterns:
        query_count = pattern.filter_count
        total_queries += query_count
        # QueryPattern is already aggregated at table+column granularity.
        filter_column_counts[pattern.column_name] = (
            filter_column_counts.get(pattern.column_name, 0) + query_count
        )
        if pattern.avg_selectivity is not None:
            selectivity_by_column[pattern.column_name] = float(pattern.avg_selectivity)

    if not filter_column_counts or total_queries <= 0:
        return _bucket_rec(patterns_n=len(patterns))

    # Case 1: wrong column — a dominant filter column isn't in the partition spec at all.
    most_filtered_column = max(filter_column_counts.items(), key=lambda x: x[1])
    filter_column, filter_count = most_filtered_column
    filter_pct = (filter_count / total_queries) * 100
    is_partitioned_by_filter_col = filter_column.lower() in current_spec.lower()
    scan_ratio_trigger = (
        scan_ratio is not None
        and scan_ratio > _SCAN_RATIO_THRESHOLD
        and scan_query_n >= _SCAN_RATIO_MIN_QUERIES
        and filter_pct >= _SCAN_RATIO_MIN_FILTER_PCT
    )
    frequency_trigger = filter_pct >= 80
    trigger_name = (
        "scan_ratio" if scan_ratio_trigger and not frequency_trigger else "filter_frequency"
    )

    if not is_partitioned_by_filter_col and (frequency_trigger or scan_ratio_trigger):
        cardinality = cardinality_hint(filter_column)
        warnings: list[str] = []

        if cardinality == "high":
            # Partitioning a near-unique column with `identity` creates one partition per
            # distinct value — the over-partitioning anti-pattern. Recommend bucket()
            # instead, which still prunes on equality filters without that blowup.
            bucket_count = _bucket_count_for(total_size_gb)
            new_field = f"{filter_column} (bucket[{bucket_count}])"
            reason = (
                f"{filter_pct:.0f}% of queries filter on '{filter_column}' but the table isn't "
                f"partitioned by it. '{filter_column}' looks high-cardinality by name, so "
                f"bucket({bucket_count}) is recommended instead of identity — partitioning a "
                "near-unique column with identity would create one partition per distinct "
                "value."
            )
        else:
            new_field = f"{filter_column} (identity)"
            reason = (
                f"{filter_pct:.0f}% of queries filter on '{filter_column}' "
                "but table is not partitioned by this column."
            )
            if cardinality == "unknown":
                warnings.append(
                    f"Cardinality of '{filter_column}' could not be estimated from its name; "
                    "verify it isn't near-unique before applying identity partitioning."
                )

        if scan_ratio is not None:
            floor = (
                " — above the 10% pruning-effectiveness floor"
                if scan_ratio > _SCAN_RATIO_THRESHOLD
                else ""
            )
            reason += (
                f" Scan ratio (avg bytes read / table size) is {scan_ratio:.0%} "
                f"across {scan_query_n} queries{floor}."
            )

        recommended_spec = (
            f"{current_spec}, {new_field}" if current_spec != "unpartitioned" else new_field
        )

        return PartitionEvolutionRecommendation(
            table_id=str(tid),
            table_name=table_name,
            current_partition_spec=current_spec,
            recommended_partition_spec=recommended_spec,
            reason=reason,
            confidence=min(0.95, filter_pct / 100) * (0.85 if cardinality == "unknown" else 1.0),
            estimated_pruning_improvement_pct=filter_pct * 0.7,
            query_patterns_analyzed=len(patterns),
            cardinality_estimate=cardinality,
            warnings=warnings,
            scan_ratio=scan_ratio,
            avg_selectivity=selectivity_by_column.get(filter_column),
            trigger=trigger_name,
        )

    # Case 2: granularity mismatch — table is partitioned by a coarser time transform than
    # its queries actually filter at (e.g., year-partitioned but day-level query filters).
    time_field = _time_partition_field(raw_partition_spec)
    if time_field is not None:
        current_column, current_transform, current_transform_raw = time_field
        for candidate in _finer_transforms(current_transform):
            fine_filter_count = sum(
                p.range_filter_count for p in patterns if candidate in p.column_name.lower()
            )
            ratio = fine_filter_count / total_queries
            if ratio <= 0.6:
                continue

            avg_gb_after = _estimated_avg_partition_gb_after_switch(
                total_size_gb, stats.total_partitions, current_transform, candidate
            )
            if avg_gb_after is not None and avg_gb_after < _MIN_HEALTHY_PARTITION_GB:
                # Switching would produce mostly-empty partitions — the under-partitioning
                # anti-pattern. Don't recommend it; the data volume doesn't support it yet.
                continue

            recommended_spec = current_spec.replace(f"({current_transform_raw})", f"({candidate})")
            size_note = (
                f" Estimated avg partition size after switching stays at "
                f"~{avg_gb_after * 1024:.0f}MB, above the "
                f"{_MIN_HEALTHY_PARTITION_GB * 1024:.0f}MB healthy minimum."
                if avg_gb_after is not None
                else ""
            )

            return PartitionEvolutionRecommendation(
                table_id=str(tid),
                table_name=table_name,
                current_partition_spec=current_spec,
                recommended_partition_spec=recommended_spec,
                reason=(
                    f"{ratio * 100:.0f}% of queries use {candidate}-level filters but "
                    f"'{current_column}' is partitioned by {current_transform}.{size_note}"
                ),
                confidence=0.85,
                estimated_pruning_improvement_pct=60.0,
                query_patterns_analyzed=len(patterns),
                cardinality_estimate="low",  # time columns are always low-cardinality
                scan_ratio=scan_ratio,
                trigger="granularity",
            )

    return _bucket_rec(patterns_n=len(patterns))


def get_all_partition_recommendations(
    session: Session,
    min_query_threshold: int = 100,
) -> list[PartitionEvolutionRecommendation]:
    """
    Get partition evolution recommendations for all tables.

    Args:
        session: Database session
        min_query_threshold: Minimum queries needed per table

    Returns:
        List of recommendations sorted by confidence
    """
    stmt = (
        select(Table.id)
        .join(QueryPattern, Table.id == QueryPattern.table_id)
        .group_by(Table.id)
        .having(func.count(QueryPattern.id) >= min_query_threshold)
    )

    table_ids = session.execute(stmt).scalars().all()

    recommendations = []
    for table_id in table_ids:
        try:
            rec = analyze_partition_evolution(session, str(table_id), min_query_threshold)
            if rec:
                recommendations.append(rec)
        except Exception:
            continue

    recommendations.sort(key=lambda r: r.confidence, reverse=True)

    return recommendations
