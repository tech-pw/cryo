"""Partition evolution recommendations engine.

Analyzes query patterns to recommend partition spec changes
for better query pruning and performance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import QueryPattern, Table, TableStats


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


def analyze_partition_evolution(
    session: Session,
    table_id: str,
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
    table = session.get(Table, table_id)
    if not table:
        raise ValueError(f"Table {table_id} not found")
    
    stats = session.get(TableStats, table_id)
    if not stats:
        return None
    
    table_name = f"{table.catalog}.{table.schema_name}.{table.table_name}"
    
    # Get query patterns for this table
    pattern_stmt = (
        select(QueryPattern)
        .where(QueryPattern.table_id == table_id)
        .where(QueryPattern.query_count >= 10)  # Only consider frequent patterns
    )
    patterns = session.execute(pattern_stmt).scalars().all()
    
    if len(patterns) < min_query_threshold:
        return None  # Not enough query data for recommendation
    
    # Analyze filter patterns
    filter_column_counts: dict[str, int] = {}
    total_queries = 0
    
    for pattern in patterns:
        query_count = pattern.query_count
        total_queries += query_count
        
        # Count which columns appear in filters
        if pattern.filter_columns:
            for filter_col in pattern.filter_columns:
                if isinstance(filter_col, dict):
                    col_name = filter_col.get("column", "")
                elif isinstance(filter_col, str):
                    col_name = filter_col
                else:
                    continue
                
                filter_column_counts[col_name] = filter_column_counts.get(col_name, 0) + query_count
    
    if not filter_column_counts:
        return None  # No filter patterns found
    
    # Get current partition spec from raw stats
    current_spec = "unpartitioned"
    if stats.raw and isinstance(stats.raw, dict):
        partition_spec = stats.raw.get("partition_spec", [])
        if partition_spec and isinstance(partition_spec, list):
            current_spec = ", ".join([f"{p.get('name', '')} ({p.get('transform', '')})" for p in partition_spec])
    
    # Find most frequently filtered column
    most_filtered_column = max(filter_column_counts.items(), key=lambda x: x[1])
    filter_column, filter_count = most_filtered_column
    filter_pct = (filter_count / total_queries) * 100
    
    # Check if this column is already in partition spec
    is_partitioned_by_filter_col = filter_column.lower() in current_spec.lower()
    
    if not is_partitioned_by_filter_col and filter_pct >= 80:
        # Strong recommendation: 80%+ of queries filter on non-partitioned column
        recommended_spec = f"{current_spec}, {filter_column} (identity)" if current_spec != "unpartitioned" else f"{filter_column} (identity)"
        
        return PartitionEvolutionRecommendation(
            table_id=str(table_id),
            table_name=table_name,
            current_partition_spec=current_spec,
            recommended_partition_spec=recommended_spec,
            reason=f"{filter_pct:.0f}% of queries filter on '{filter_column}' but table is not partitioned by this column",
            confidence=min(0.95, filter_pct / 100),
            estimated_pruning_improvement_pct=filter_pct * 0.7,  # Conservative estimate
            query_patterns_analyzed=len(patterns),
        )
    
    # Check for granularity mismatch (e.g., YEAR partition but queries use DAY ranges)
    if "year" in current_spec.lower() and filter_column.lower() in current_spec.lower():
        # Check if queries use fine-grained date filters
        daily_filter_count = sum(
            p.query_count
            for p in patterns
            if p.filter_columns and any(
                isinstance(f, dict) and f.get("is_range") and "day" in str(f.get("column", "")).lower()
                for f in p.filter_columns
            )
        )
        
        if daily_filter_count / total_queries > 0.6:
            recommended_spec = current_spec.replace("year", "day")
            
            return PartitionEvolutionRecommendation(
                table_id=str(table_id),
                table_name=table_name,
                current_partition_spec=current_spec,
                recommended_partition_spec=recommended_spec,
                reason=f"{(daily_filter_count / total_queries) * 100:.0f}% of queries use day-level filters but table is partitioned by year",
                confidence=0.85,
                estimated_pruning_improvement_pct=60.0,
                query_patterns_analyzed=len(patterns),
            )
    
    # No recommendation needed
    return None


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
    # Get all tables with query patterns
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
    
    # Sort by confidence descending
    recommendations.sort(key=lambda r: r.confidence, reverse=True)
    
    return recommendations
