"""Tests for cryo.api.routes_cost_attribution -- the query cost attribution report API."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from cryo.api.routes_cost_attribution import get_cost_attribution
from cryo.db.models import QueryCostAttributionDaily, Source


def _seed_source(session: Session) -> Source:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="hive", config={})
    session.add(source)
    session.commit()
    return source


def _seed_row(
    session: Session,
    source: Source,
    *,
    day: date,
    client: str,
    schema_name: str,
    query_count: int = 1,
    failed_query_count: int = 0,
    physical_bytes_scanned: int = 0,
    estimated_scan_cost_usd: float | None = None,
) -> None:
    session.add(
        QueryCostAttributionDaily(
            source_id=source.id,
            day=day,
            client=client,
            schema_name=schema_name,
            query_count=query_count,
            failed_query_count=failed_query_count,
            physical_bytes_scanned=physical_bytes_scanned,
            estimated_scan_cost_usd=estimated_scan_cost_usd,
        )
    )
    session.commit()


class TestGetCostAttribution:
    def test_empty_when_no_rows(self, session: Session) -> None:
        result = get_cost_attribution(session, days=7)

        assert result.rows == []
        assert result.by_client == []
        assert result.total_query_count == 0

    def test_rolls_up_bytes_and_counts_by_client_and_schema(self, session: Session) -> None:
        source = _seed_source(session)
        today = datetime.now(UTC).date()
        _seed_row(
            session,
            source,
            day=today,
            client="Metabase v0.60.0",
            schema_name="analytics",
            query_count=10,
            failed_query_count=2,
            physical_bytes_scanned=5 * 1024**3,
        )

        result = get_cost_attribution(session, days=7)

        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.client == "Metabase v0.60.0"
        assert row.schema_name == "analytics"
        assert row.query_count == 10
        assert row.failed_query_count == 2
        assert row.physical_gb_scanned == 5.0

    def test_same_client_multiple_days_and_schemas_sums_correctly(self, session: Session) -> None:
        source = _seed_source(session)
        today = datetime.now(UTC).date()
        yesterday = today - timedelta(days=1)
        _seed_row(
            session,
            source,
            day=today,
            client="Metabase",
            schema_name="analytics",
            query_count=5,
            physical_bytes_scanned=1024**3,
        )
        _seed_row(
            session,
            source,
            day=yesterday,
            client="Metabase",
            schema_name="analytics",
            query_count=3,
            physical_bytes_scanned=2 * 1024**3,
        )
        _seed_row(
            session,
            source,
            day=today,
            client="Metabase",
            schema_name="offline",
            query_count=1,
            physical_bytes_scanned=1024**2,
        )

        result = get_cost_attribution(session, days=7)

        # by_client aggregates across schemas AND days for the same client.
        assert len(result.by_client) == 1
        client_row = result.by_client[0]
        assert client_row.client == "Metabase"
        assert client_row.query_count == 9
        assert client_row.physical_gb_scanned == 3.0 + (1024**2 / 1024**3)

        # rows (per client+schema) keeps analytics and offline separate.
        assert len(result.rows) == 2

    def test_multiple_clients_ranked_by_bytes_descending(self, session: Session) -> None:
        source = _seed_source(session)
        today = datetime.now(UTC).date()
        _seed_row(
            session,
            source,
            day=today,
            client="small-client",
            schema_name="s",
            physical_bytes_scanned=1024**2,
        )
        _seed_row(
            session,
            source,
            day=today,
            client="big-client",
            schema_name="s",
            physical_bytes_scanned=100 * 1024**3,
        )

        result = get_cost_attribution(session, days=7)

        assert [r.client for r in result.by_client] == ["big-client", "small-client"]

    def test_excludes_rows_older_than_the_requested_window(self, session: Session) -> None:
        source = _seed_source(session)
        today = datetime.now(UTC).date()
        old_day = today - timedelta(days=30)
        _seed_row(
            session,
            source,
            day=old_day,
            client="stale-client",
            schema_name="s",
            physical_bytes_scanned=1024**3,
        )

        result = get_cost_attribution(session, days=7)

        assert result.rows == []

    def test_total_query_count_and_gb_are_correct_across_clients(self, session: Session) -> None:
        source = _seed_source(session)
        today = datetime.now(UTC).date()
        _seed_row(
            session,
            source,
            day=today,
            client="a",
            schema_name="s",
            query_count=10,
            physical_bytes_scanned=1024**3,
        )
        _seed_row(
            session,
            source,
            day=today,
            client="b",
            schema_name="s",
            query_count=20,
            physical_bytes_scanned=2 * 1024**3,
        )

        result = get_cost_attribution(session, days=7)

        assert result.total_query_count == 30
        assert result.total_physical_gb_scanned == 3.0
        # No stored price is not a priced zero.
        assert result.total_estimated_scan_cost_usd is None
        assert result.scan_cost_priced_days == 0
        assert result.rows[0].estimated_scan_cost_usd is None

    def test_unpriced_day_is_not_filled_in_from_a_priced_day(self, session: Session) -> None:
        source = _seed_source(session)
        today = datetime.now(UTC).date()
        _seed_row(
            session,
            source,
            day=today,
            client="Metabase",
            schema_name="analytics",
            physical_bytes_scanned=1024**3,
            estimated_scan_cost_usd=4.0,
        )
        _seed_row(
            session,
            source,
            day=today - timedelta(days=1),
            client="Metabase",
            schema_name="analytics",
            physical_bytes_scanned=9 * 1024**3,
        )
        _seed_row(
            session,
            source,
            day=today,
            client="dbt",
            schema_name="analytics",
            physical_bytes_scanned=1024**3,
            estimated_scan_cost_usd=1.5,
        )

        result = get_cost_attribution(session, days=7)

        by_client = {row.client: row for row in result.by_client}
        assert by_client["Metabase"].estimated_scan_cost_usd is None
        assert by_client["dbt"].estimated_scan_cost_usd == 1.5
        assert result.rows[0].client == "Metabase"
        assert result.rows[0].estimated_scan_cost_usd is None
        # The fleet total still prices only the days that had a price. It does not
        # pretend the unpriced day was free.
        assert result.total_estimated_scan_cost_usd == 5.5
        assert result.scan_cost_priced_days == 1

    def test_stored_zero_is_a_real_zero(self, session: Session) -> None:
        source = _seed_source(session)
        today = datetime.now(UTC).date()
        _seed_row(
            session,
            source,
            day=today,
            client="idle",
            schema_name="analytics",
            estimated_scan_cost_usd=0.0,
        )

        result = get_cost_attribution(session, days=7)

        assert result.total_estimated_scan_cost_usd == 0.0
        assert result.scan_cost_priced_days == 1
        assert result.by_client[0].estimated_scan_cost_usd == 0.0
