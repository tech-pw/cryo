"""Tests for the scheduler's telemetry ingestion and query_log retention ticks.

Covers two real gaps found while investigating query-log bloat:
- _maybe_ingest_telemetry previously only ever ingested the first enabled Source
  (`.limit(1)`) and never aggregated into query_patterns, so the scheduled path produced raw
  query_logs rows nobody's recommendations engine ever consumed unless a human separately
  clicked "ingest" in the UI for that same source.
- there was no retention/purge logic anywhere for query_logs, so the table only ever grew.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from cryo.db.models import QueryLog, Source, Table
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler.service import SchedulerService
from cryo.telemetry.patterns import purge_old_query_logs
from tests.conftest import (
    _assign_missing_autoincrement_primary_keys,
    _assign_missing_uuid_primary_keys,
)


def _bound_session_factory(session):
    """A sessionmaker bound to the test session's engine, with the same autoincrement/UUID
    primary-key event listeners the `session` fixture itself uses -- needed because the
    scheduler code under test opens its own sessions via `SessionLocal()` rather than reusing
    the fixture's session object directly."""
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    event.listen(factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    return factory


def _seed_source(session, *, enabled: bool = True) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={"catalog_name": "lake"},
        enabled=enabled,
    )
    session.add(source)
    session.flush()
    return source


def _seed_table(session, source: Source, *, table_name: str) -> Table:
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.flush()
    return table


def _fake_query_row(query_id: str, query: str) -> dict:
    return {
        "query_id": query_id,
        "query": query,
        "user_name": "test",
        "executed_at": datetime.now(UTC).isoformat(),
        "queued_time_ms": 0,
        "analysis_time_ms": 0,
        "planning_time_ms": 0,
        "execution_time_ms": 10,
        "physical_input_rows": 100,
        "physical_input_bytes": 1000,
    }


class _NullStore:
    def list_due_presets(self, now):
        return []

    def list_tables(self):
        return []

    def list_presets(self):
        return []

    def list_rules(self):
        return []

    def list_assignments(self):
        return []

    def list_destinations(self):
        return []

    def persist_run(self, run):
        pass

    def mark_preset_run(self, preset_id, run_at):
        pass


def _make_service() -> SchedulerService:
    return SchedulerService(_NullStore(), InMemoryMaintenanceQueue())


def test_ingest_telemetry_covers_every_enabled_source(session, monkeypatch) -> None:
    # aggregate_query_patterns uses a Postgres-only ON CONFLICT DO UPDATE ... RETURNING id
    # upsert (see tests/test_telemetry_patterns.py, which stubs it out for the same reason) --
    # not runnable against this suite's SQLite session fixture. Spy on it instead of letting it
    # run for real, so this test can still verify the actual scheduler-level fix: every enabled
    # source gets ingested *and* aggregated, not just the first one.
    aggregate_calls: list[object] = []
    monkeypatch.setattr(
        "cryo.telemetry.patterns.aggregate_query_patterns",
        lambda session, table_id=None: aggregate_calls.append(table_id) or 0,
    )

    source_a = _seed_source(session)
    source_b = _seed_source(session)
    _seed_table(session, source_a, table_name="orders")
    _seed_table(session, source_b, table_name="shipments")

    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    fake_settings = type(
        "FakeSettings",
        (),
        {
            "telemetry_trino_url": "http://trino:8080",
            "telemetry_ingest_on_startup": True,
            "query_log_retention_days": 90,
        },
    )()
    monkeypatch.setattr("cryo.config.get_settings", lambda: fake_settings)

    async def _fake_fetch(since=None, limit=1000):
        return [
            _fake_query_row("q1", "SELECT * FROM analytics.orders WHERE order_id = 1"),
            _fake_query_row("q2", "SELECT * FROM analytics.shipments WHERE region = 'us'"),
        ]

    with patch(
        "cryo.telemetry.ingestion.TrinoQueryIngester.fetch_recent_queries",
        new=AsyncMock(side_effect=_fake_fetch),
    ):
        service = _make_service()
        service._maybe_ingest_telemetry(datetime.now(UTC))

    with session_factory() as verify_session:
        logs_by_source = {row.source_id for row in verify_session.query(QueryLog).all()}
        assert source_a.id in logs_by_source
        assert source_b.id in logs_by_source

    # The scheduled tick must aggregate once per source, not just write raw rows.
    assert len(aggregate_calls) == 2


def test_purge_query_logs_removes_only_stale_rows(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, table_name="events")

    old_row = QueryLog(
        source_id=source.id,
        table_id=table.id,
        query_id="old",
        engine="trino",
        query_text="SELECT 1",
        executed_at=datetime.now(UTC) - timedelta(days=120),
    )
    recent_row = QueryLog(
        source_id=source.id,
        table_id=table.id,
        query_id="recent",
        engine="trino",
        query_text="SELECT 1",
        executed_at=datetime.now(UTC) - timedelta(days=5),
    )
    session.add_all([old_row, recent_row])
    session.flush()

    deleted = purge_old_query_logs(session, retention_days=90)

    remaining_ids = {row.query_id for row in session.query(QueryLog).all()}
    assert deleted == 1
    assert remaining_ids == {"recent"}


def test_purge_query_logs_tick_respects_daily_cadence(session, monkeypatch) -> None:
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    fake_settings = type("FakeSettings", (), {"query_log_retention_days": 90})()
    monkeypatch.setattr("cryo.config.get_settings", lambda: fake_settings)

    calls = []
    monkeypatch.setattr(
        "cryo.telemetry.patterns.purge_old_query_logs",
        lambda session, retention_days: calls.append(retention_days) or 0,
    )

    service = _make_service()
    now = datetime.now(UTC)
    service._maybe_purge_query_logs(now)
    service._maybe_purge_query_logs(now + timedelta(hours=1))  # too soon, should skip
    assert calls == [90]

    service._maybe_purge_query_logs(now + timedelta(days=2))  # due again
    assert calls == [90, 90]
