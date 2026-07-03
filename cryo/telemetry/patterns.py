"""Query pattern aggregation and analysis."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cryo.db.models import QueryLog, QueryPattern, Table

logger = logging.getLogger(__name__)


@dataclass
class ColumnPatternStats:
    """Aggregated statistics for a column's query patterns."""

    column_name: str
    filter_count: int = 0
    equality_filter_count: int = 0
    range_filter_count: int = 0
    join_count: int = 0
    projection_count: int = 0
    avg_selectivity: float | None = None
    total_queries: int = 0


@dataclass
class TablePatternSummary:
    """Summary of query patterns for a table."""

    table_id: uuid.UUID
    schema_name: str
    table_name: str
    total_queries: int
    columns: list[ColumnPatternStats]
    dominant_filter_column: str | None = None
    multi_column_filter_ratio: float = 0.0  # % of queries filtering on multiple columns


def aggregate_query_patterns(
    session: Session,
    table_id: uuid.UUID | None = None,
    since: datetime | None = None,
) -> int:
    """Aggregate query logs into query patterns.

    Args:
        session: Database session
        table_id: Optional table ID to aggregate for (None = all tables)
        since: Only consider queries since this time

    Returns:
        Number of patterns updated
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=30)

    # Build query to get all relevant query logs
    query = select(QueryLog).where(
        QueryLog.executed_at >= since,
        QueryLog.table_id.isnot(None),
    )
    if table_id:
        query = query.where(QueryLog.table_id == table_id)

    logs = session.scalars(query).all()
    logger.info(f"Aggregating patterns from {len(logs)} query logs")

    # Group by table and aggregate column stats
    table_stats: defaultdict[uuid.UUID, dict[str, ColumnPatternStats]] = defaultdict(dict)

    for log in logs:
        if log.table_id is None:
            continue

        stats_by_col = table_stats[log.table_id]

        # Process filter columns
        for col_info in log.filter_columns or []:
            col_name = col_info.get("column", "")
            if not col_name:
                continue

            if col_name not in stats_by_col:
                stats_by_col[col_name] = ColumnPatternStats(column_name=col_name)

            stats = stats_by_col[col_name]
            stats.filter_count += 1
            stats.total_queries += 1

            if col_info.get("is_equality"):
                stats.equality_filter_count += 1
            if col_info.get("is_range"):
                stats.range_filter_count += 1

        # Process join columns
        for col_info in log.join_columns or []:
            col_name = col_info.get("column", "")
            if not col_name:
                continue

            if col_name not in stats_by_col:
                stats_by_col[col_name] = ColumnPatternStats(column_name=col_name)

            stats_by_col[col_name].join_count += 1

        # Process projection columns
        for col_info in log.projection_columns or []:
            col_name = col_info.get("column", "")
            if not col_name:
                continue

            if col_name not in stats_by_col:
                stats_by_col[col_name] = ColumnPatternStats(column_name=col_name)

            stats_by_col[col_name].projection_count += 1

    # Upsert patterns into database
    updated = 0
    for tid, columns in table_stats.items():
        for col_name, stats in columns.items():
            # Use PostgreSQL upsert
            stmt = insert(QueryPattern).values(
                table_id=tid,
                column_name=col_name,
                filter_count=stats.filter_count,
                equality_filter_count=stats.equality_filter_count,
                range_filter_count=stats.range_filter_count,
                join_count=stats.join_count,
                projection_count=stats.projection_count,
                last_seen_at=datetime.now(UTC),
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_query_patterns_table_column",
                set_={
                    "filter_count": stmt.excluded.filter_count,
                    "equality_filter_count": stmt.excluded.equality_filter_count,
                    "range_filter_count": stmt.excluded.range_filter_count,
                    "join_count": stmt.excluded.join_count,
                    "projection_count": stmt.excluded.projection_count,
                    "last_seen_at": stmt.excluded.last_seen_at,
                    "updated_at": func.now(),
                },
            )
            session.execute(stmt)
            updated += 1

    session.commit()
    logger.info(f"Updated {updated} query patterns")
    return updated


def get_table_query_patterns(
    session: Session,
    table_id: uuid.UUID,
    min_filter_count: int = 1,
) -> TablePatternSummary:
    """Get query pattern summary for a table.

    Args:
        session: Database session
        table_id: Table ID
        min_filter_count: Minimum filter count to include a column

    Returns:
        TablePatternSummary with column statistics
    """
    table = session.get(Table, table_id)
    if table is None:
        raise ValueError(f"Table not found: {table_id}")

    patterns = session.scalars(
        select(QueryPattern)
        .where(QueryPattern.table_id == table_id)
        .order_by(QueryPattern.filter_count.desc())
    ).all()

    # Count total queries for this table
    total_queries = (
        session.scalar(select(func.count(QueryLog.id)).where(QueryLog.table_id == table_id)) or 0
    )

    # Count queries with multiple filter columns
    multi_filter_count = (
        session.scalar(
            select(func.count(QueryLog.id)).where(
                QueryLog.table_id == table_id,
                func.jsonb_array_length(QueryLog.filter_columns) > 1,
            )
        )
        or 0
    )

    multi_column_ratio = multi_filter_count / total_queries if total_queries > 0 else 0.0

    columns = [
        ColumnPatternStats(
            column_name=p.column_name,
            filter_count=p.filter_count,
            equality_filter_count=p.equality_filter_count,
            range_filter_count=p.range_filter_count,
            join_count=p.join_count,
            projection_count=p.projection_count,
            avg_selectivity=p.avg_selectivity,
            total_queries=p.filter_count + p.join_count,
        )
        for p in patterns
        if p.filter_count >= min_filter_count
    ]

    # Determine dominant filter column (if one column is used in >60% of filtered queries)
    dominant_column = None
    if columns and total_queries > 0:
        top_column = columns[0]
        if top_column.filter_count / total_queries > 0.6:
            dominant_column = top_column.column_name

    return TablePatternSummary(
        table_id=table_id,
        schema_name=table.schema_name,
        table_name=table.table_name,
        total_queries=total_queries,
        columns=columns,
        dominant_filter_column=dominant_column,
        multi_column_filter_ratio=multi_column_ratio,
    )


def get_recommended_sort_columns(
    patterns: TablePatternSummary,
    max_columns: int = 4,
) -> tuple[list[str], str]:
    """Recommend sort columns based on query patterns.

    Args:
        patterns: Table query pattern summary
        max_columns: Maximum number of columns to recommend

    Returns:
        Tuple of (recommended_columns, strategy) where strategy is 'sort' or 'zorder'
    """
    if not patterns.columns:
        return [], "none"

    # If there's a dominant filter column, use linear sort
    if patterns.dominant_filter_column:
        # Find columns most often used with the dominant column
        columns = [patterns.dominant_filter_column]
        for col in patterns.columns:
            if col.column_name != patterns.dominant_filter_column and len(columns) < max_columns:
                if col.filter_count > patterns.total_queries * 0.1:  # >10% of queries
                    columns.append(col.column_name)
        return columns, "sort"

    # If multi-column filtering is common (>30% of queries), use z-order
    if patterns.multi_column_filter_ratio > 0.3:
        # Take top N filter columns
        columns = [
            col.column_name
            for col in sorted(patterns.columns, key=lambda c: c.filter_count, reverse=True)[
                :max_columns
            ]
            if col.filter_count > patterns.total_queries * 0.05  # >5% of queries
        ]
        if len(columns) >= 2:
            return columns, "zorder"

    # Default: linear sort on top filter columns
    columns = [
        col.column_name
        for col in sorted(patterns.columns, key=lambda c: c.filter_count, reverse=True)[
            :max_columns
        ]
        if col.filter_count > 0
    ]
    return columns, "sort" if columns else "none"
