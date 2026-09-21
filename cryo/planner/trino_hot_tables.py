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

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import QueryLog, Table

_TRINO_ENGINE = "trino"


@dataclass(frozen=True)
class TrinoHotTable:
    table_id: uuid.UUID
    trino_query_count: int


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

    Only tables with at least one Trino query in the window are returned -- a table with zero
    Trino traffic has no Trino cost to reduce by maintaining it, so it has no business in a
    preset whose entire purpose is Trino-cost reduction, however you'd rank a zero.
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

    return [
        TrinoHotTable(table_id=table_id, trino_query_count=int(count)) for table_id, count in rows
    ]
