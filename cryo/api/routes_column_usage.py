"""How queries use each column of one table, from the aggregated ``query_patterns``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from cryo.api.deps import SessionDep, require_viewer
from cryo.db.models import QueryPattern, Table, TableStats
from cryo.db.tenant_scope import get_scoped_resource

router = APIRouter(prefix="/api/v1", tags=["tables"])

WINDOW_DAYS = 30
# The aggregator rewrites every column it saw in a tick and leaves the rest untouched, so a row
# much older than the newest one describes an earlier window.
_SAME_TICK = timedelta(hours=6)


class ColumnUsageRead(BaseModel):
    column: str
    filter_count: int
    equality_filter_count: int
    range_filter_count: int
    join_count: int
    projection_count: int
    avg_selectivity: float | None


class ColumnUsageResponse(BaseModel):
    table_id: uuid.UUID
    window_days: int
    queries: int | None
    aggregated_at: datetime | None
    columns: list[ColumnUsageRead]


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@router.get(
    "/tables/{table_id}/column-usage",
    response_model=ColumnUsageResponse,
    dependencies=[Depends(require_viewer)],
)
def get_table_column_usage(table_id: uuid.UUID, session: SessionDep) -> ColumnUsageResponse:
    get_scoped_resource(session, Table, table_id, resource_type="table")
    rows = list(session.scalars(select(QueryPattern).where(QueryPattern.table_id == table_id)))
    stats = session.get(TableStats, table_id)
    newest = max((_utc(row.last_seen_at) for row in rows), default=None)
    current = [
        row for row in rows if newest is not None and newest - _utc(row.last_seen_at) <= _SAME_TICK
    ]
    current.sort(key=lambda row: (row.filter_count + row.join_count, row.projection_count))
    current.reverse()
    return ColumnUsageResponse(
        table_id=table_id,
        window_days=WINDOW_DAYS,
        queries=stats.query_count_30d if stats is not None else None,
        aggregated_at=newest,
        columns=[
            ColumnUsageRead(
                column=row.column_name,
                filter_count=row.filter_count,
                equality_filter_count=row.equality_filter_count,
                range_filter_count=row.range_filter_count,
                join_count=row.join_count,
                projection_count=row.projection_count,
                avg_selectivity=row.avg_selectivity,
            )
            for row in current
        ],
    )
