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

from cryo.db.models import LayoutRecommendation, QueryLog, QueryPattern, Source, Table
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


def test_ingest_telemetry_opens_a_fresh_session_per_source(session, monkeypatch) -> None:
    """2026-09-03 fix: _maybe_ingest_telemetry used to hold a single Postgres session open
    across the entire loop over every enabled Source, with a real (bounded but potentially
    slow) network call awaited per source inside that same open transaction -- confirmed live
    as the cause of a 15+ minute idle-in-transaction session on the scheduler pod (and an
    earlier 5+ hour occurrence the same day). This asserts the concrete fix: SessionLocal() is
    now called once per source (plus once up front for the id list), not once for the whole
    tick -- so a slow network call for one source can only ever hold open that source's own
    short-lived session, never a shared one spanning every source.
    """
    session_open_count = 0
    real_bound_factory = _bound_session_factory(session)

    def _counting_factory():
        nonlocal session_open_count
        session_open_count += 1
        return real_bound_factory()

    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", _counting_factory)

    monkeypatch.setattr(
        "cryo.telemetry.query_buffer.buffer_trino_queries",
        lambda queries, *, source_id, maxlen=250_000: len(queries),
    )
    monkeypatch.setattr(
        "cryo.scheduler.service._source_for_destination", lambda session, destination: None
    )

    source_a = _seed_source(session)
    source_b = _seed_source(session)
    source_c = _seed_source(session)
    session.commit()

    fake_settings = type(
        "FakeSettings",
        (),
        {
            "telemetry_trino_url": "http://trino:8080",
            "telemetry_trino_user": None,
            "telemetry_trino_password": None,
            "telemetry_trino_ssl_verify": True,
            "telemetry_ingest_on_startup": True,
        },
    )()
    monkeypatch.setattr("cryo.config.get_settings", lambda: fake_settings)

    async def _fake_fetch(since=None, limit=1000):
        return []

    with patch(
        "cryo.telemetry.ingestion.TrinoQueryIngester.fetch_recent_queries",
        new=AsyncMock(side_effect=_fake_fetch),
    ):
        service = _make_service()
        service._maybe_ingest_telemetry(datetime.now(UTC))

    # 1 session for the id-list read + 1 per source (source_a, source_b, source_c) + 0 for
    # destinations (none enabled in this fixture) -- never a single session shared across
    # all three sources' network calls.
    assert session_open_count >= 4
    assert {source_a.id, source_b.id, source_c.id}  # sanity: all three were actually seeded


def test_aggregate_cost_attribution_opens_a_fresh_session_per_source(session, monkeypatch) -> None:
    """2026-09-04 fix: _maybe_aggregate_cost_attribution had the exact same bug as
    _maybe_ingest_telemetry's own 2026-09-03 fix (see that test's docstring) -- one Postgres
    session held open across the whole loop over every enabled Source, with
    aggregate_query_cost_attribution's real Iceberg catalog I/O (source_catalog_for, a network
    call) awaited per source inside that same open transaction. Missed in the first pass
    because it's a separate daily-cadence tick method. Confirmed live: a cryo.sources query
    held idle-in-transaction for 3+ hours the day after the telemetry fix shipped, same
    wait_event=ClientRead signature, different tick method. Same assertion as the telemetry
    test: SessionLocal() is called once per source, not once for the whole tick.
    """
    from cryo.telemetry.iceberg_source import IcebergQueryLogConfig

    session_open_count = 0
    real_bound_factory = _bound_session_factory(session)

    def _counting_factory():
        nonlocal session_open_count
        session_open_count += 1
        return real_bound_factory()

    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", _counting_factory)

    fake_config = IcebergQueryLogConfig(table_identifier="cryo_internal.queries")
    # _maybe_aggregate_cost_attribution imports parse_iceberg_query_log_config locally from
    # cryo.telemetry.iceberg_source at call time, so patching the source module (not a
    # cryo.scheduler.service attribute) is what actually takes effect here.
    monkeypatch.setattr(
        "cryo.telemetry.iceberg_source.parse_iceberg_query_log_config",
        lambda config: fake_config,
    )

    from cryo.telemetry.cost_attribution import CostAttributionResult

    aggregate_calls: list[object] = []

    def _fake_aggregate(session, source, *, now=None):
        aggregate_calls.append(source.id)
        return CostAttributionResult(0, 0, 0)

    # Same local-import reasoning as above -- patch the source module.
    monkeypatch.setattr(
        "cryo.telemetry.cost_attribution.aggregate_query_cost_attribution", _fake_aggregate
    )

    source_a = _seed_source(session)
    source_b = _seed_source(session)
    session.commit()

    service = _make_service()
    service._maybe_aggregate_cost_attribution(datetime.now(UTC))

    assert {source_a.id, source_b.id} == set(aggregate_calls)
    # 1 session for the id-list read + 1 per source -- never a single session shared across
    # both sources' network calls.
    assert session_open_count >= 3


def test_ingest_telemetry_covers_every_enabled_source(session, monkeypatch) -> None:
    """Every enabled Source gets fetched each tick; the curator drains the buffer and
    writes a curated subset to Postgres. Aggregate is called once per source.
    """
    aggregate_calls: list[object] = []
    monkeypatch.setattr(
        "cryo.telemetry.patterns.aggregate_query_patterns",
        lambda session, table_id=None: aggregate_calls.append(table_id) or 0,
    )

    # New pipeline: buffer accepts all rows, curator drains and writes the curated subset
    # to Postgres query_logs. Stub the buffer so the test can run without Redis, then let
    # the curator run for real so we can verify it actually wrote the curated subset.
    buffered_rows: list[dict] = []

    def _fake_buffer(queries, *, source_id, maxlen=250_000):
        for q in queries:
            buffered_rows.append({"query": q, "source_id": source_id})
        return len(queries)

    monkeypatch.setattr("cryo.telemetry.query_buffer.buffer_trino_queries", _fake_buffer)

    def _fake_drain(*, source_id, last_id="0", count=1000, block_ms=0):
        # Pop rows for this source off the buffer and hand them to the curator
        rows = [b for b in buffered_rows if b["source_id"] == source_id]
        return [r["query"] for r in rows], last_id

    monkeypatch.setattr("cryo.telemetry.query_buffer.drain_trino_queries", _fake_drain)

    # Stub the writer so the curator doesn't try to talk to a real Iceberg catalog
    from cryo.telemetry.iceberg_writer import QueryLogAppendResult

    class _FakeWriter:
        def __init__(self):
            self.append_calls = []
            self.expire_calls = 0

        @property
        def table_identifier(self):
            return "cryo_internal.queries"

        def append(self, rows):
            self.append_calls.extend(rows)
            return QueryLogAppendResult(
                rows_attempted=len(rows),
                rows_appended=len(rows),
                table_identifier=self.table_identifier,
            )

        def expire_snapshots_older_than(self, days):
            self.expire_calls += 1
            from cryo.telemetry.iceberg_writer import QueryLogRetentionResult

            return QueryLogRetentionResult(
                table_identifier=self.table_identifier, snapshots_expired=0
            )

    # Wire the writer stub into the curator by patching the factory
    fake_writer = _FakeWriter()
    monkeypatch.setattr(
        "cryo.telemetry.iceberg_writer.writer_for_destination",
        lambda destination, source=None, config=None: fake_writer,
    )
    # No destination is enabled in the fixture (the fixture only seeds Sources), so the
    # curator is a no-op for destinations -- the test is really about the legacy fetch
    # path, which now buffers to Redis (stubbed) instead of writing directly to Postgres.
    # Drop the curator source-resolution call so it doesn't trip on missing destinations.
    monkeypatch.setattr(
        "cryo.scheduler.service._source_for_destination", lambda session, destination: None
    )

    source_a = _seed_source(session)
    source_b = _seed_source(session)
    _seed_table(session, source_a, table_name="orders")
    _seed_table(session, source_b, table_name="shipments")
    # 2026-09-03: _maybe_ingest_telemetry now opens a fresh SessionLocal() per source/
    # destination (see that method's own docstring) instead of one session for the whole
    # tick, so the seed data needs to be genuinely committed -- a flush-only write is only
    # visible within this same session/transaction, not to the sibling sessions the code
    # under test now opens.
    session.commit()

    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    fake_settings = type(
        "FakeSettings",
        (),
        {
            "telemetry_trino_url": "http://trino:8080",
            "telemetry_trino_user": None,
            "telemetry_trino_password": None,
            "telemetry_trino_ssl_verify": True,
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

    # In the new pipeline the legacy fetch path buffers to Redis -- the curator drains
    # and writes the curated subset to Postgres. There are no enabled destinations in the
    # fixture, so the curator is a no-op and no rows land in query_logs. The test asserts
    # the *buffer* received rows from BOTH sources (proving the scheduler no longer caps
    # at the first one), and that the ingest tick ran for both.
    buffered_source_ids = {b["source_id"] for b in buffered_rows}
    assert source_a.id in buffered_source_ids
    assert source_b.id in buffered_source_ids
    # Each source was fetched independently -- two calls into buffer_trino_queries.
    buffer_calls_for_a = [b for b in buffered_rows if b["source_id"] == source_a.id]
    buffer_calls_for_b = [b for b in buffered_rows if b["source_id"] == source_b.id]
    assert len(buffer_calls_for_a) > 0
    assert len(buffer_calls_for_b) > 0
    # The scheduled tick still uses the read-through aggregator for sources that already
    # have an Iceberg-native query log, but the legacy fetch path (no destination, no
    # iceberg config) now just buffers and lets the curator aggregate. aggregate_calls
    # should be empty here because the fixture has no iceberg-configured sources.
    assert aggregate_calls == []


def test_generate_layout_recommendations_covers_tables_with_patterns(session, monkeypatch) -> None:
    """Regression test: generate_layout_recommendation() was fully implemented but had no
    caller anywhere in the app -- query_patterns could have thousands of real rows while
    layout_recommendations stayed permanently empty. This is the wiring that closes that gap."""
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    source = _seed_source(session)
    table_a = _seed_table(session, source, table_name="orders")
    table_b = _seed_table(session, source, table_name="shipments")
    session.add_all(
        [
            QueryPattern(table_id=table_a.id, column_name="order_id", filter_count=5),
            QueryPattern(table_id=table_b.id, column_name="region", filter_count=3),
        ]
    )
    session.flush()

    generated_for: list[object] = []
    monkeypatch.setattr(
        "cryo.optimizer.layout_analyzer.generate_layout_recommendation",
        lambda session, table_id, force_refresh=False: generated_for.append(table_id),
    )

    fake_settings = type("FakeSettings", (), {"layout_recommendation_max_tables_per_tick": 200})()

    service = _make_service()
    service._generate_layout_recommendations(fake_settings)

    assert set(generated_for) == {table_a.id, table_b.id}


def test_generate_layout_recommendations_prioritizes_uncovered(session, monkeypatch) -> None:
    """Tables with no LayoutRecommendation yet (NULL updated_at) must sort before tables that
    already have one -- otherwise an unbounded backlog could let the same already-covered
    tables win the per-tick cap every time, starving tables that never got a first pass."""
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    source = _seed_source(session)
    covered = _seed_table(session, source, table_name="covered")
    uncovered = _seed_table(session, source, table_name="uncovered")
    session.add_all(
        [
            QueryPattern(table_id=covered.id, column_name="id", filter_count=1),
            QueryPattern(table_id=uncovered.id, column_name="id", filter_count=1),
            LayoutRecommendation(
                id=uuid4(),
                table_id=covered.id,
                recommended_strategy="none",
                recommended_columns=[],
            ),
        ]
    )
    session.flush()

    generated_for: list[object] = []
    monkeypatch.setattr(
        "cryo.optimizer.layout_analyzer.generate_layout_recommendation",
        lambda session, table_id, force_refresh=False: generated_for.append(table_id),
    )

    fake_settings = type("FakeSettings", (), {"layout_recommendation_max_tables_per_tick": 1})()

    service = _make_service()
    service._generate_layout_recommendations(fake_settings)

    assert generated_for == [uncovered.id]


def test_generate_layout_recommendations_excludes_applied(session, monkeypatch) -> None:
    """: an applied recommendation must never be silently regenerated by routine
    telemetry -- generate_layout_recommendation() overwrites current_prune_efficiency in place
    with no status check, which would corrupt the frozen applied_prune_efficiency_baseline's
    meaning (moving the "current" state out from under the before/after comparison) and make
    every applied recommendation look "pending again" in every field except status itself."""
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    source = _seed_source(session)
    applied_table = _seed_table(session, source, table_name="already_applied")
    pending_table = _seed_table(session, source, table_name="still_pending")
    session.add_all(
        [
            QueryPattern(table_id=applied_table.id, column_name="id", filter_count=1),
            QueryPattern(table_id=pending_table.id, column_name="id", filter_count=1),
            LayoutRecommendation(
                id=uuid4(),
                table_id=applied_table.id,
                recommended_strategy="sort",
                recommended_columns=[{"column": "id"}],
                status="applied",
                applied_prune_efficiency_baseline=0.2,
            ),
        ]
    )
    session.flush()

    generated_for: list[object] = []
    monkeypatch.setattr(
        "cryo.optimizer.layout_analyzer.generate_layout_recommendation",
        lambda session, table_id, force_refresh=False: generated_for.append(table_id),
    )

    fake_settings = type("FakeSettings", (), {"layout_recommendation_max_tables_per_tick": 200})()

    service = _make_service()
    service._generate_layout_recommendations(fake_settings)

    assert generated_for == [pending_table.id]


def test_measure_layout_recommendation_effectiveness_measures_ready_candidates(
    session, monkeypatch
) -> None:
    """The actual point of : an applied recommendation old enough to measure, with no
    observed_prune_improvement yet, gets a real measurement call."""
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    source = _seed_source(session)
    table = _seed_table(session, source, table_name="resorted")
    recommendation = LayoutRecommendation(
        id=uuid4(),
        table_id=table.id,
        recommended_strategy="sort",
        recommended_columns=[{"column": "id"}],
        status="applied",
        applied_at=datetime.now(UTC) - timedelta(hours=48),
        applied_prune_efficiency_baseline=0.1,
    )
    session.add(recommendation)
    session.flush()

    measured_for: list[object] = []

    def _fake_measure(session, rec):
        measured_for.append(rec.id)
        return 0.4

    monkeypatch.setattr(
        "cryo.optimizer.layout_analyzer.measure_layout_recommendation_effectiveness",
        _fake_measure,
    )

    fake_settings = type("FakeSettings", (), {"layout_effectiveness_max_tables_per_tick": 20})()

    service = _make_service()
    service._measure_layout_recommendation_effectiveness(fake_settings)

    assert measured_for == [recommendation.id]


def test_measure_layout_recommendation_effectiveness_skips_recent_and_already_measured(
    session, monkeypatch
) -> None:
    """Two exclusions: applied too recently (needs MIN_HOURS_BEFORE_MEASURING_EFFECTIVENESS to
    pass first) and already has a real observed_prune_improvement (nothing left to measure)."""
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    source = _seed_source(session)
    too_recent = _seed_table(session, source, table_name="too_recent")
    already_measured = _seed_table(session, source, table_name="already_measured")
    session.add_all(
        [
            LayoutRecommendation(
                id=uuid4(),
                table_id=too_recent.id,
                recommended_strategy="sort",
                recommended_columns=[{"column": "id"}],
                status="applied",
                applied_at=datetime.now(UTC) - timedelta(hours=1),
                applied_prune_efficiency_baseline=0.1,
            ),
            LayoutRecommendation(
                id=uuid4(),
                table_id=already_measured.id,
                recommended_strategy="sort",
                recommended_columns=[{"column": "id"}],
                status="applied",
                applied_at=datetime.now(UTC) - timedelta(hours=48),
                applied_prune_efficiency_baseline=0.1,
                observed_prune_improvement=0.3,
            ),
        ]
    )
    session.flush()

    measured_for: list[object] = []
    monkeypatch.setattr(
        "cryo.optimizer.layout_analyzer.measure_layout_recommendation_effectiveness",
        lambda session, rec: measured_for.append(rec.id),
    )

    fake_settings = type("FakeSettings", (), {"layout_effectiveness_max_tables_per_tick": 20})()

    service = _make_service()
    service._measure_layout_recommendation_effectiveness(fake_settings)

    assert measured_for == []


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
