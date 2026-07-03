"""Layout analysis and recommendation engine.

Analyzes table file statistics and query patterns to recommend
optimal data layout (sort order, z-ordering).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import LayoutRecommendation, QueryPattern, Table, TableStats
from cryo.telemetry.patterns import get_recommended_sort_columns, get_table_query_patterns

logger = logging.getLogger(__name__)


class LayoutStrategy(str, Enum):
    """Data layout optimization strategy."""

    NONE = "none"  # No optimization needed/possible
    SORT = "sort"  # Linear sort on one or more columns
    ZORDER = "zorder"  # Z-order clustering for multi-column filters


@dataclass
class ColumnStats:
    """Statistics for a single column across all files."""

    column_name: str
    min_values: list[Any] = field(default_factory=list)
    max_values: list[Any] = field(default_factory=list)
    distinct_ranges: int = 0  # Number of unique (min, max) ranges
    overlap_ratio: float = 1.0  # How much file ranges overlap (1.0 = complete overlap)
    prune_potential: float = 0.0  # Estimated % of files that could be pruned


@dataclass
class LayoutAnalysis:
    """Analysis results for a table's data layout."""

    table_id: uuid.UUID
    schema_name: str
    table_name: str
    total_files: int
    total_size_bytes: int

    # Current layout metrics
    current_sort_order: list[str] | None = None
    column_stats: list[ColumnStats] = field(default_factory=list)

    # Pruning efficiency
    current_prune_efficiency: float = 0.0  # Current % of files pruned on average
    best_prune_efficiency: float = 0.0  # Best possible with perfect clustering
    efficiency_gap: float = 0.0  # Potential improvement

    # Recommendations
    recommended_strategy: LayoutStrategy = LayoutStrategy.NONE
    recommended_columns: list[str] = field(default_factory=list)
    estimated_improvement: float = 0.0
    confidence: float = 0.0

    # Query pattern summary
    total_queries_analyzed: int = 0
    multi_column_filter_ratio: float = 0.0

    # Analysis metadata
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def analyze_table_layout(
    session: Session,
    table_id: uuid.UUID,
    file_stats: list[dict[str, Any]] | None = None,
) -> LayoutAnalysis:
    """Analyze a table's data layout and generate recommendations.

    Args:
        session: Database session
        table_id: Table to analyze
        file_stats: Optional pre-fetched file statistics (from $files metadata table)

    Returns:
        LayoutAnalysis with current metrics and recommendations
    """
    table = session.get(Table, table_id)
    if table is None:
        raise ValueError(f"Table not found: {table_id}")

    stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))

    analysis = LayoutAnalysis(
        table_id=table_id,
        schema_name=table.schema_name,
        table_name=table.table_name,
        total_files=stats.total_files if stats else 0,
        total_size_bytes=int((stats.total_size_gb or 0) * 1024**3) if stats else 0,
    )

    # Get current sort order from table metadata (if available)
    raw_stats = dict(stats.raw or {}) if stats else {}
    analysis.current_sort_order = raw_stats.get("sort_order")

    # Analyze file statistics for column overlap
    if file_stats:
        analysis.column_stats = _analyze_column_stats(file_stats)
        analysis.current_prune_efficiency = _calculate_current_efficiency(analysis.column_stats)

    # Get query patterns
    try:
        patterns = get_table_query_patterns(session, table_id)
        analysis.total_queries_analyzed = patterns.total_queries
        analysis.multi_column_filter_ratio = patterns.multi_column_filter_ratio

        # Get recommended columns based on query patterns
        recommended_cols, strategy = get_recommended_sort_columns(patterns)
        analysis.recommended_columns = recommended_cols
        analysis.recommended_strategy = LayoutStrategy(strategy)

        # Estimate improvement
        if recommended_cols:
            analysis.estimated_improvement = _estimate_improvement(
                analysis.current_prune_efficiency,
                patterns,
                recommended_cols,
            )
            analysis.confidence = _calculate_confidence(patterns, len(recommended_cols))

    except ValueError:
        logger.warning(f"No query patterns available for table {table_id}")

    analysis.efficiency_gap = max(0, analysis.best_prune_efficiency - analysis.current_prune_efficiency)

    return analysis


def _analyze_column_stats(file_stats: list[dict[str, Any]]) -> list[ColumnStats]:
    """Analyze per-column statistics from file metadata."""
    from collections import defaultdict

    column_ranges: dict[str, list[tuple[Any, Any]]] = defaultdict(list)

    for file_info in file_stats:
        # Extract column bounds from file stats
        lower_bounds = file_info.get("lower_bounds", {})
        upper_bounds = file_info.get("upper_bounds", {})

        for col_name in set(lower_bounds.keys()) | set(upper_bounds.keys()):
            low = lower_bounds.get(col_name)
            high = upper_bounds.get(col_name)
            if low is not None and high is not None:
                column_ranges[col_name].append((low, high))

    result = []
    for col_name, ranges in column_ranges.items():
        stats = ColumnStats(
            column_name=col_name,
            min_values=[r[0] for r in ranges],
            max_values=[r[1] for r in ranges],
            distinct_ranges=len(set(ranges)),
        )

        # Calculate overlap ratio
        if ranges:
            stats.overlap_ratio = _calculate_overlap_ratio(ranges)
            stats.prune_potential = 1.0 - stats.overlap_ratio

        result.append(stats)

    # Sort by prune potential (columns with less overlap are more useful for pruning)
    result.sort(key=lambda c: c.prune_potential, reverse=True)
    return result


def _calculate_overlap_ratio(ranges: list[tuple[Any, Any]]) -> float:
    """Calculate how much file ranges overlap.

    Returns 0 if no overlap, 1 if complete overlap.
    """
    if len(ranges) < 2:
        return 0.0

    try:
        # Find global min/max
        all_mins = [r[0] for r in ranges]
        all_maxs = [r[1] for r in ranges]
        global_min = min(all_mins)
        global_max = max(all_maxs)
        global_range = global_max - global_min

        if global_range == 0:
            return 1.0  # All same value

        # Calculate average file range as fraction of global
        avg_file_range = sum(r[1] - r[0] for r in ranges) / len(ranges)
        ideal_file_range = global_range / len(ranges)

        # Overlap ratio = how much bigger average file range is than ideal
        if ideal_file_range > 0:
            overlap = min(1.0, avg_file_range / global_range)
        else:
            overlap = 1.0

        return overlap

    except (TypeError, ValueError):
        # Can't compare these values (e.g., strings, mixed types)
        return 0.5


def _calculate_current_efficiency(column_stats: list[ColumnStats]) -> float:
    """Calculate current file pruning efficiency from column stats."""
    if not column_stats:
        return 0.0

    # Use the best column's prune potential as an estimate
    best_prune = max(c.prune_potential for c in column_stats) if column_stats else 0.0
    return best_prune


def _estimate_improvement(
    current_efficiency: float,
    patterns: Any,  # TablePatternSummary
    recommended_columns: list[str],
) -> float:
    """Estimate pruning efficiency improvement from applying recommended layout."""
    if not recommended_columns:
        return 0.0

    # Base improvement: assume well-clustered data can achieve 80%+ pruning
    target_efficiency = 0.8

    # Adjust based on query pattern confidence
    if patterns.total_queries < 10:
        target_efficiency *= 0.7  # Less confident with few queries

    if patterns.multi_column_filter_ratio > 0.5:
        # Z-order is less efficient than linear sort, but better for multi-column
        target_efficiency *= 0.9

    improvement = max(0, target_efficiency - current_efficiency)
    return round(improvement, 3)


def _calculate_confidence(patterns: Any, num_columns: int) -> float:
    """Calculate confidence in the recommendation."""
    confidence = 0.0

    # More queries = more confidence
    if patterns.total_queries >= 100:
        confidence += 0.4
    elif patterns.total_queries >= 30:
        confidence += 0.3
    elif patterns.total_queries >= 10:
        confidence += 0.2
    else:
        confidence += 0.1

    # Clear dominant column = more confidence
    if patterns.dominant_filter_column:
        confidence += 0.3

    # Fewer recommended columns = more confidence (simpler is better)
    if num_columns == 1:
        confidence += 0.3
    elif num_columns <= 3:
        confidence += 0.2
    else:
        confidence += 0.1

    return min(1.0, confidence)


def calculate_prune_efficiency(
    total_files: int,
    files_read: int,
) -> float:
    """Calculate file pruning efficiency.

    Args:
        total_files: Total files in table
        files_read: Files actually read for a query

    Returns:
        Efficiency as fraction (0-1) where 1 = perfect pruning
    """
    if total_files <= 0:
        return 0.0
    if files_read <= 0:
        return 1.0  # All files pruned

    files_pruned = total_files - files_read
    return files_pruned / total_files


def generate_layout_recommendation(
    session: Session,
    table_id: uuid.UUID,
    force_refresh: bool = False,
) -> LayoutRecommendation:
    """Generate and store a layout recommendation for a table.

    Args:
        session: Database session
        table_id: Table to analyze
        force_refresh: If True, regenerate even if recent recommendation exists

    Returns:
        LayoutRecommendation record
    """
    # Check for existing recommendation
    existing = session.scalar(
        select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
    )

    # If recent and not forcing refresh, return existing
    if existing and not force_refresh:
        age_hours = (datetime.now(UTC) - existing.updated_at).total_seconds() / 3600
        if age_hours < 24:  # Less than 24 hours old
            return existing

    # Analyze table layout
    analysis = analyze_table_layout(session, table_id)

    # Create or update recommendation
    if existing:
        existing.current_sort_order = [{"column": c} for c in (analysis.current_sort_order or [])]
        existing.recommended_strategy = analysis.recommended_strategy.value
        existing.recommended_columns = [{"column": c} for c in analysis.recommended_columns]
        existing.estimated_prune_improvement = analysis.estimated_improvement
        existing.current_prune_efficiency = analysis.current_prune_efficiency
        existing.analysis_query_count = analysis.total_queries_analyzed
        existing.confidence = analysis.confidence
        existing.detail = {
            "multi_column_filter_ratio": analysis.multi_column_filter_ratio,
            "efficiency_gap": analysis.efficiency_gap,
            "total_files": analysis.total_files,
            "total_size_bytes": analysis.total_size_bytes,
        }
        recommendation = existing
    else:
        recommendation = LayoutRecommendation(
            table_id=table_id,
            current_sort_order=[{"column": c} for c in (analysis.current_sort_order or [])],
            recommended_strategy=analysis.recommended_strategy.value,
            recommended_columns=[{"column": c} for c in analysis.recommended_columns],
            estimated_prune_improvement=analysis.estimated_improvement,
            current_prune_efficiency=analysis.current_prune_efficiency,
            analysis_query_count=analysis.total_queries_analyzed,
            confidence=analysis.confidence,
            status="pending",
            detail={
                "multi_column_filter_ratio": analysis.multi_column_filter_ratio,
                "efficiency_gap": analysis.efficiency_gap,
                "total_files": analysis.total_files,
                "total_size_bytes": analysis.total_size_bytes,
            },
        )
        session.add(recommendation)

    session.commit()
    session.refresh(recommendation)
    return recommendation
