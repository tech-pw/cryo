"""Tests for cryo.api.routes_partition_access -- the per-table partition access API
(docs/PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from cryo.api.routes_partition_access import get_partition_access
from cryo.db.models import PartitionAccessDaily, Source, Table


def _seed_source(session: Session) -> Source:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="hive", config={})
    session.add(source)
    session.commit()
    return source


def _seed_table(session: Session, source: Source, *, name: str = "a") -> Table:
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=name,
        is_active=True,
        location=f"s3://bucket/warehouse/analytics/{name}",
    )
    session.add(table)
    session.commit()
    return table


def _seed_row(
    session: Session,
    table: Table,
    *,
    day: date,
    partition_path: str,
    request_count: int = 1,
    requester_role: str | None = None,
) -> None:
    now = datetime.now(UTC)
    session.add(
        PartitionAccessDaily(
            table_id=table.id,
            day=day,
            partition_path=partition_path,
            requester_role=requester_role,
            request_count=request_count,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    session.commit()


class TestGetPartitionAccess:
    def test_404_for_unknown_table(self, session: Session) -> None:
        with pytest.raises(HTTPException) as exc_info:
            get_partition_access(session, uuid.uuid4())
        assert exc_info.value.status_code == 404

    def test_empty_when_no_rows(self, session: Session) -> None:
        source = _seed_source(session)
        table = _seed_table(session, source)

        result = get_partition_access(session, table.id)

        assert result.table_id == table.id
        assert result.points == []
        assert result.partitions == []

    def test_returns_one_point_per_day_partition(self, session: Session) -> None:
        source = _seed_source(session)
        table = _seed_table(session, source)
        today = datetime.now(UTC).date()
        _seed_row(session, table, day=today, partition_path="dt=2026-08-19/", request_count=10)
        _seed_row(session, table, day=today, partition_path="dt=2026-08-18/", request_count=3)

        result = get_partition_access(session, table.id, days=7)

        assert len(result.points) == 2
        paths = {p.partition_path: p.request_count for p in result.points}
        assert paths == {"dt=2026-08-19/": 10, "dt=2026-08-18/": 3}

    def test_collapses_requester_role_dimension_into_one_point(self, session: Session) -> None:
        """The API response is one point per (day, partition) for the scatter chart -- the
        underlying table's requester_role dimension is summed away here (a per-role breakdown
        is explicitly out of scope for this endpoint, see the plan doc)."""
        source = _seed_source(session)
        table = _seed_table(session, source)
        today = datetime.now(UTC).date()
        _seed_row(
            session,
            table,
            day=today,
            partition_path="dt=2026-08-19/",
            request_count=5,
            requester_role="arn:aws:iam::123:user/alice",
        )
        _seed_row(
            session,
            table,
            day=today,
            partition_path="dt=2026-08-19/",
            request_count=3,
            requester_role="arn:aws:iam::123:user/bob",
        )

        result = get_partition_access(session, table.id, days=7)

        assert len(result.points) == 1
        assert result.points[0].request_count == 8

    def test_partition_summary_computes_days_since_last_access(self, session: Session) -> None:
        source = _seed_source(session)
        table = _seed_table(session, source)
        today = datetime.now(UTC).date()
        stale_day = today - timedelta(days=10)
        _seed_row(session, table, day=stale_day, partition_path="dt=old/", request_count=1)

        result = get_partition_access(session, table.id, days=30)

        assert len(result.partitions) == 1
        summary = result.partitions[0]
        assert summary.partition_path == "dt=old/"
        assert summary.total_requests == 1
        assert summary.first_seen == stale_day
        assert summary.last_seen == stale_day
        assert summary.days_since_last_access == 10

    def test_partition_summary_sums_across_multiple_days(self, session: Session) -> None:
        source = _seed_source(session)
        table = _seed_table(session, source)
        today = datetime.now(UTC).date()
        yesterday = today - timedelta(days=1)
        _seed_row(session, table, day=yesterday, partition_path="dt=x/", request_count=4)
        _seed_row(session, table, day=today, partition_path="dt=x/", request_count=6)

        result = get_partition_access(session, table.id, days=7)

        assert len(result.partitions) == 1
        summary = result.partitions[0]
        assert summary.total_requests == 10
        assert summary.first_seen == yesterday
        assert summary.last_seen == today
        assert summary.days_since_last_access == 0

    def test_partitions_ranked_most_recently_accessed_first(self, session: Session) -> None:
        source = _seed_source(session)
        table = _seed_table(session, source)
        today = datetime.now(UTC).date()
        _seed_row(
            session,
            table,
            day=today - timedelta(days=20),
            partition_path="dt=cold/",
            request_count=1,
        )
        _seed_row(session, table, day=today, partition_path="dt=hot/", request_count=1)

        result = get_partition_access(session, table.id, days=30)

        assert [p.partition_path for p in result.partitions] == ["dt=hot/", "dt=cold/"]

    def test_excludes_rows_older_than_the_requested_window(self, session: Session) -> None:
        source = _seed_source(session)
        table = _seed_table(session, source)
        old_day = datetime.now(UTC).date() - timedelta(days=100)
        _seed_row(session, table, day=old_day, partition_path="dt=ancient/", request_count=1)

        result = get_partition_access(session, table.id, days=7)

        assert result.points == []
        assert result.partitions == []

    def test_does_not_leak_another_tables_partitions(self, session: Session) -> None:
        source = _seed_source(session)
        table_a = _seed_table(session, source, name="a")
        table_b = _seed_table(session, source, name="b")
        today = datetime.now(UTC).date()
        _seed_row(session, table_a, day=today, partition_path="dt=a/", request_count=1)
        _seed_row(session, table_b, day=today, partition_path="dt=b/", request_count=1)

        result = get_partition_access(session, table_a.id, days=7)

        assert len(result.points) == 1
        assert result.points[0].partition_path == "dt=a/"
