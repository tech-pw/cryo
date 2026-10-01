"""Partition access aggregates and caps in SQL, not after loading every row."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Select, event
from sqlalchemy.orm import Session

from cryo.api.routes_partition_access import (
    _MAX_PARTITIONS,
    _MAX_POINTS,
    get_partition_access,
    partition_access_points_statement,
    partition_access_summary_statement,
)
from cryo.db.models import PartitionAccessDaily, Source, Table


def _param_values(parameters: object) -> list[object]:
    if parameters is None:
        return []
    if isinstance(parameters, dict):
        return list(parameters.values())
    if isinstance(parameters, (list, tuple)):
        values: list[object] = []
        for item in parameters:
            if isinstance(item, (list, tuple, dict)):
                values.extend(_param_values(item))
            else:
                values.append(item)
        return values
    return [parameters]


def _seed_table(session: Session) -> Table:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="hive", config={})
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name="events",
        is_active=True,
        location="s3://bucket/warehouse/analytics/events",
    )
    session.add(source)
    session.add(table)
    session.commit()
    return table


def _sql(statement: Select[object]) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True})).lower()


def test_summary_and_points_statements_aggregate_and_limit() -> None:
    table_id = uuid.uuid4()
    since = date(2026, 1, 1)
    summary = _sql(partition_access_summary_statement(table_id, since))
    points = _sql(partition_access_points_statement(table_id, since, ["dt=0001/"]))

    assert "group by" in summary
    assert "sum(" in summary
    assert f"limit {_MAX_PARTITIONS}" in summary
    assert "group by" in points
    assert "sum(" in points
    assert f"limit {_MAX_POINTS}" in points
    assert "in (" in points


def test_large_row_set_is_aggregated_in_the_query(session: Session) -> None:
    table = _seed_table(session)
    today = datetime.now(UTC).date()
    stale = today - timedelta(days=20)
    now = datetime.now(UTC)
    rows: list[PartitionAccessDaily] = []
    for index in range(600):
        path = f"dt={index:04d}/"
        day = today if index < 500 else stale
        roles = 3 if index == 0 else 1
        for role_index in range(roles):
            rows.append(
                PartitionAccessDaily(
                    table_id=table.id,
                    day=day,
                    partition_path=path,
                    requester_role=f"role-{role_index}",
                    request_count=2,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
    session.add_all(rows)
    session.commit()

    executed: list[tuple[str, object]] = []

    def _capture(
        _conn: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        executed.append((statement, parameters))

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        result = get_partition_access(session, table.id, days=30)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    selects = [
        (statement.lower(), parameters)
        for statement, parameters in executed
        if "partition_access_daily" in statement.lower()
        and statement.lstrip().lower().startswith("select")
    ]
    assert len(selects) == 2
    assert all(
        "sum(" in statement and "group by" in statement and "limit" in statement
        for statement, _ in selects
    )
    assert not any(
        "request_count" in statement and "sum(" not in statement for statement, _ in selects
    )
    bound_limits = {value for _, parameters in selects for value in _param_values(parameters)}
    assert _MAX_PARTITIONS in bound_limits
    assert _MAX_POINTS in bound_limits

    assert len(result.partitions) == _MAX_PARTITIONS
    assert all(summary.partition_path != "dt=0599/" for summary in result.partitions)
    hot = next(summary for summary in result.partitions if summary.partition_path == "dt=0000/")
    assert hot.total_requests == 6
    assert all(point.partition_path != "dt=0599/" for point in result.points)
    hot_points = [point for point in result.points if point.partition_path == "dt=0000/"]
    assert len(hot_points) == 1
    assert hot_points[0].request_count == 6
