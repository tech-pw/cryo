"""Query pattern aggregation and analysis."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cryo.db.models import QueryLog, QueryPattern, Table, TableQueryActivity, TableStats

logger = logging.getLogger(__name__)


@dataclass
class ColumnPatternStats:
    """Aggregated statistics for a column's query patterns.

    ``avg_selectivity`` is the running mean of ``rows matched / table's total rows`` across
    every query observed for this column, lower is better pruning (see
    ``cryo.optimizer.pto.candidate_space.PtoPattern.pruning_value``, which falls back to 0.5
    -- "no information" -- whenever this is ``None``). It's accumulated via
    ``record_selectivity_sample`` rather than assigned directly so repeated calls (e.g. one
    per matched column per query) produce a correct mean instead of overwriting each other;
    ``_selectivity_sample_count`` is the running-mean's own weight and is intentionally not
    the same number as ``total_queries`` (a query can touch a column without Cryo being able
    to compute that query's selectivity -- see ``iceberg_source._selectivity_for_query``).
    """

    column_name: str
    filter_count: int = 0
    equality_filter_count: int = 0
    range_filter_count: int = 0
    join_count: int = 0
    projection_count: int = 0
    avg_selectivity: float | None = None
    total_queries: int = 0
    _selectivity_sample_count: int = 0

    def record_selectivity_sample(self, selectivity: float) -> None:
        """Fold one more (query, selectivity) observation into the running mean."""
        selectivity = min(1.0, max(0.0, selectivity))
        if self.avg_selectivity is None:
            self.avg_selectivity = selectivity
            self._selectivity_sample_count = 1
            return
        n = self._selectivity_sample_count
        self.avg_selectivity = (self.avg_selectivity * n + selectivity) / (n + 1)
        self._selectivity_sample_count = n + 1


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

        # files_read/files_pruned are populated by the same engines that populate
        # filter_columns (see cryo/telemetry/ingestion.py's TrinoQueryIngester), so a query
        # that pruned files gives a real, engine-reported selectivity signal -- how much of
        # the table's file set this query actually had to touch. Files rather than rows
        # because that's what both Trino's query-completion event and this table's own
        # columns natively carry; a files-scanned ratio is a reasonable proxy for a
        # rows-scanned ratio for the same partition-pruning purpose PtoPattern uses it for.
        # getattr rather than log.files_read: real QueryLog rows always have the attribute
        # (nullable column), but callers/tests sometimes pass lighter row stand-ins that only
        # set the fields a given test cares about.
        query_selectivity: float | None = None
        files_read = getattr(log, "files_read", None) or 0
        files_pruned = getattr(log, "files_pruned", None) or 0
        total_candidate_files = files_read + files_pruned
        if total_candidate_files > 0:
            query_selectivity = files_read / total_candidate_files

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
            if query_selectivity is not None:
                stats.record_selectivity_sample(query_selectivity)

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

    updated = _upsert_query_patterns(session, table_stats)
    logger.info(f"Updated {updated} query patterns")

    # This function re-reads the same query_logs rows on every call (no watermark -- the curator
    # calls it fresh every scheduler tick with the default 30-day window), so day buckets must be
    # REPLACED with the recomputed truth, not incremented, or every tick would double-count.
    _replace_table_query_activity_from_query_logs(session, logs)
    refresh_query_count_30d(session, set(table_stats.keys()))
    _refresh_wair_query_ranges(session, logs)
    return updated


def _refresh_wair_query_ranges(session: Session, logs: Sequence[QueryLog]) -> None:
    grouped: defaultdict[uuid.UUID, dict[tuple[str, str, str], dict[str, Any]]] = defaultdict(dict)
    for log in logs:
        raw = getattr(log, "raw", None)
        if log.table_id is None or not isinstance(raw, dict):
            continue
        ranges = raw.get("query_ranges")
        if not isinstance(ranges, list):
            continue
        for item in ranges:
            if not isinstance(item, dict):
                continue
            column = item.get("column")
            lower = item.get("lower")
            upper = item.get("upper")
            if not isinstance(column, str) or lower is None or upper is None:
                continue
            key = (column, repr(lower), repr(upper))
            aggregate = grouped[log.table_id].setdefault(
                key,
                {"column": column, "lower": lower, "upper": upper, "frequency": 0},
            )
            aggregate["frequency"] += 1

    for target_table_id, ranges_by_key in grouped.items():
        stats = session.get(TableStats, target_table_id)
        if stats is None:
            continue
        ranked = sorted(
            ranges_by_key.values(),
            key=lambda item: (
                -int(item["frequency"]),
                str(item["column"]),
                repr(item["lower"]),
                repr(item["upper"]),
            ),
        )[:100]
        stats.raw = {**dict(stats.raw or {}), "wair_query_ranges": ranked}
    if grouped:
        session.commit()


def _replace_table_query_activity_from_query_logs(
    session: Session,
    logs: Sequence[QueryLog],
) -> None:
    day_counts: defaultdict[tuple[uuid.UUID, date], int] = defaultdict(int)
    for log in logs:
        if log.table_id is None or log.executed_at is None:
            continue
        day_counts[(log.table_id, log.executed_at.date())] += 1

    for (table_id, day), count in day_counts.items():
        existing = (
            session.query(TableQueryActivity).filter_by(table_id=table_id, day=day).one_or_none()
        )
        if existing is None:
            session.add(TableQueryActivity(table_id=table_id, day=day, query_count=count))
        else:
            existing.query_count = count
            existing.updated_at = datetime.now(UTC)
    session.commit()


def _upsert_query_patterns(
    session: Session,
    table_stats: dict[uuid.UUID, dict[str, ColumnPatternStats]],
) -> int:
    """Shared upsert step for both `aggregate_query_patterns` (reads from query_logs) and
    `cryo.telemetry.iceberg_source.aggregate_from_iceberg_query_log` (reads straight from an
    existing Iceberg query-log table) -- both build the same per-(table, column) stats shape,
    only the source of the raw query text differs."""
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
                avg_selectivity=stats.avg_selectivity,
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
                    # NULL only when this call observed zero selectivity samples for the
                    # column (e.g. every matched query lacked usable stats) -- COALESCE keeps
                    # whatever was already stored rather than blanking out a real prior value.
                    # Every other field here is already a blind per-call overwrite (not an
                    # incremental merge), so this matches existing semantics as closely as
                    # possible while not regressing a populated value to NULL for a quiet call.
                    "avg_selectivity": func.coalesce(
                        stmt.excluded.avg_selectivity, QueryPattern.avg_selectivity
                    ),
                    "last_seen_at": stmt.excluded.last_seen_at,
                    "updated_at": func.now(),
                },
            )
            session.execute(stmt)
            updated += 1

    session.commit()
    return updated


def record_table_query_activity(
    session: Session,
    query_counts: dict[uuid.UUID, int],
    *,
    day: date | None = None,
) -> None:
    """Upsert day-bucketed per-table query counts (see TableQueryActivity's docstring for why
    this exists separately from query_patterns). Additive on conflict: a table can legitimately
    be touched by more than one aggregation window/tick on the same day, and each call only
    knows about the queries *it* just processed, not the day's running total."""
    if not query_counts:
        return
    bucket = day or datetime.now(UTC).date()
    for table_id, count in query_counts.items():
        if count <= 0:
            continue
        existing = (
            session.query(TableQueryActivity).filter_by(table_id=table_id, day=bucket).one_or_none()
        )
        if existing is None:
            session.add(TableQueryActivity(table_id=table_id, day=bucket, query_count=count))
        else:
            existing.query_count += count
            existing.updated_at = datetime.now(UTC)
    session.commit()


def refresh_query_count_30d(
    session: Session,
    table_ids: set[uuid.UUID],
    *,
    now: datetime | None = None,
) -> int:
    """Recompute TableStats.query_count_30d from TableQueryActivity's rolling 30-day sum for
    the given tables. Only updates tables that already have a TableStats row -- a table with no
    stats yet gets its query_count_30d set on its next scan instead (scanner/run.py carries the
    then-current TableStats.query_count_30d forward, so this must run before or independent of
    that, not depend on it)."""
    if not table_ids:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=30)
    rows = session.execute(
        select(TableQueryActivity.table_id, func.sum(TableQueryActivity.query_count))
        .where(
            TableQueryActivity.table_id.in_(table_ids),
            TableQueryActivity.day >= cutoff.date(),
        )
        .group_by(TableQueryActivity.table_id)
    ).all()
    sums: dict[uuid.UUID, int] = {table_id: int(query_count or 0) for table_id, query_count in rows}
    updated = 0
    for table_id in table_ids:
        stats = session.get(TableStats, table_id)
        if stats is None:
            continue
        stats.query_count_30d = int(sums.get(table_id, 0))
        updated += 1
    session.commit()
    return updated


def purge_old_query_logs(session: Session, retention_days: int) -> int:
    """Delete raw query_logs rows older than retention_days.

    Safe to run on a schedule: query_patterns (what partition/layout recommendations actually
    read) is a separate, already-aggregated table and is unaffected by this. Callers should
    aggregate recent data (aggregate_query_patterns) *before* purging so nothing recent is
    dropped without first being rolled up -- this function itself does not aggregate.

    Args:
        session: Database session
        retention_days: Delete rows with executed_at older than this many days

    Returns:
        Number of rows deleted
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    result = session.execute(delete(QueryLog).where(QueryLog.executed_at < cutoff))
    session.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    logger.info(f"Purged {deleted} query_logs rows older than {retention_days}d")
    return deleted


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
