"""Top-N tables by real Trino query volume (not the engine-agnostic TableStats.query_count_30d).

Built for the daily top-500-by-Trino-frequency Spark maintenance preset: that preset exists
specifically to cut Trino cost by keeping the tables Trino actually scans most in good shape, so
ranking needs to isolate Trino's own query volume, not a blended "queried by any engine" count.
TableStats.query_count_30d (via TableQueryActivity) is deliberately engine-agnostic -- see that
model's own docstring -- and QueryCostAttributionDaily is schema-level, not per-table (see its
docstring) -- so neither can answer "top N tables by Trino query count" on its own. This queries
QueryLog directly, the one table that carries both `table_id` and `engine` per query.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import QueryLog, Table, TableQueryActivity

logger = logging.getLogger(__name__)

_TRINO_ENGINE = "trino"


@dataclass(frozen=True)
class TrinoHotTable:
    table_id: uuid.UUID
    trino_query_count: int
    # Which telemetry table the count came from: "query_logs" (engine-attributed, preferred)
    # or "table_query_activity" (engine-agnostic fallback). Callers log this so an operator
    # can tell a true Trino ranking from a best-effort one.
    source: str = "query_logs"


def top_trino_query_tables(
    session: Session,
    *,
    limit: int = 500,
    window_days: int = 30,
    now: datetime | None = None,
) -> list[TrinoHotTable]:
    """Rank active tables by count of distinct Trino queries touching them over the trailing
    `window_days`, most-queried first. Ties break on table_id for a stable, reproducible order
    across calls on the same data (matters for a daily re-pin job: an unstable tie order would
    make the pinned set flap between runs for tables with genuinely equal query counts).

    Only tables with at least one query in the window are returned -- a table with zero
    traffic has no Trino cost to reduce by maintaining it, so it has no business in a
    preset whose entire purpose is Trino-cost reduction, however you'd rank a zero.

    Falls back to TableQueryActivity when QueryLog has nothing in the window. QueryLog is the
    only table carrying both `table_id` and `engine`, so it stays the preferred source -- but
    it is not always written: a Source configured with query_log.mode == "iceberg_table" goes
    through aggregate_from_iceberg_query_log, which increments TableQueryActivity and
    deliberately writes no query_logs rows at all. On such a deployment the engine-precise
    query returns nothing, and returning [] would silently unpin the entire preset and leave
    the fleet's most-queried tables unmaintained -- a far worse outcome than ranking on an
    engine-agnostic count. TableQueryActivity carries no engine column, so the fallback ranks
    all query traffic; on a Trino-fronted lakehouse that is very nearly the same ordering.
    """
    current = now or datetime.now(UTC)
    window_start = current - timedelta(days=window_days)

    rows = session.execute(
        select(QueryLog.table_id, func.count(QueryLog.id))
        .join(Table, Table.id == QueryLog.table_id)
        .where(
            QueryLog.engine == _TRINO_ENGINE,
            QueryLog.table_id.isnot(None),
            QueryLog.executed_at >= window_start,
            QueryLog.executed_at < current,
            Table.is_active.is_(True),
        )
        .group_by(QueryLog.table_id)
        .order_by(func.count(QueryLog.id).desc(), QueryLog.table_id.asc())
        .limit(limit)
    ).all()

    if rows:
        return [
            TrinoHotTable(table_id=table_id, trino_query_count=int(count))
            for table_id, count in rows
        ]

    activity_rows = session.execute(
        select(TableQueryActivity.table_id, func.sum(TableQueryActivity.query_count))
        .join(Table, Table.id == TableQueryActivity.table_id)
        .where(
            TableQueryActivity.day >= window_start.date(),
            TableQueryActivity.day <= current.date(),
            Table.is_active.is_(True),
        )
        .group_by(TableQueryActivity.table_id)
        .having(func.sum(TableQueryActivity.query_count) > 0)
        .order_by(func.sum(TableQueryActivity.query_count).desc(), TableQueryActivity.table_id)
        .limit(limit)
    ).all()

    if activity_rows:
        logger.warning(
            "No Trino-attributed query_logs rows in the last %dd -- ranking the top %d hot "
            "tables on engine-agnostic table_query_activity instead. Expected when the source "
            "uses query_log.mode='iceberg_table'; check telemetry ingest if it is not.",
            window_days,
            limit,
        )
    return [
        TrinoHotTable(
            table_id=table_id,
            trino_query_count=int(count),
            source="table_query_activity",
        )
        for table_id, count in activity_rows
    ]
