"""Regression coverage for GET /sla-status (cryo/api/dashboard_routes.py::get_sla_status).

Real production bug: the per-table loop called resolve_effective_preset() (which itself
re-queries every Preset/PresetRule/TableAssignment from scratch), plus a MAX(AuditLog.ts) and
a percentile_cont(QueryLog) query, for *every table in the fleet* -- roughly 5 SQL statements
per table, ~65k queries for a ~13k-table catalog, on every page load (the page also polls every
60s). The fix batches all three into queries issued once, outside the loop.

Uses a real Postgres session, not the in-memory SQLite `session` fixture the rest of the suite
runs on: percentile_cont() is a Postgres-only aggregate that SQLite can't even parse, so this
endpoint was structurally untestable against SQLite -- very likely why it shipped with zero
test coverage in the first place. Connects to CRYO_DATABASE_URL (the migrated database CI
provides) or the local default, and skips cleanly if neither is reachable.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session as SessionCls

from cryo.api.dashboard_routes import get_sla_status
from cryo.db.models import (
    AuditLog,
    Destination,
    Preset,
    PresetRule,
    QueryLog,
    Source,
    Table,
    TableAssignment,
    TableStats,
)

_PG_URL = "postgresql+psycopg://cryo:cryo@localhost:5432/cryo"


@pytest.fixture()
def postgres_session():
    try:
        engine = create_engine(os.environ.get("CRYO_DATABASE_URL") or _PG_URL)
        connection = engine.connect()
    except OperationalError:
        pytest.skip("local Postgres not available at localhost:5432 (see Makefile: migrate)")
    transaction = connection.begin()
    session = SessionCls(bind=connection, autoflush=False, expire_on_commit=False)
    session.execute(text("SET search_path TO cryo, public"))
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def _seed_source(session: Session) -> Source:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(
    session: Session, source: Source, *, scan_at: datetime, schema_name: str = "analytics"
) -> Table:
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name=schema_name,
            table_name=f"t_{table_id.hex[:8]}",
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=1,
            total_size_gb=1.0,
            query_count_30d=0,
        )
    )
    session.flush()
    table = session.get(Table, table_id)
    assert table is not None
    return table


class _QueryCounter:
    def __init__(self, session: Session) -> None:
        self.count = 0
        self._engine = session.get_bind()

    def __enter__(self) -> _QueryCounter:
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self._engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, *args: object, **kwargs: object) -> None:
        self.count += 1


def _call(session: Session, *, breached_only: bool = False):
    return get_sla_status(session, {}, breached_only=breached_only, table_id=None)


def test_sla_status_query_count_does_not_scale_with_table_count(postgres_session) -> None:
    session = postgres_session
    source = _seed_source(session)
    now = datetime.now(UTC)

    for _ in range(3):
        _seed_table(session, source, scan_at=now - timedelta(hours=1))
    session.flush()

    with _QueryCounter(session) as counter:
        _call(session)
    small_count = counter.count

    for _ in range(15):
        _seed_table(session, source, scan_at=now - timedelta(hours=1))
    session.flush()

    with _QueryCounter(session) as counter:
        _call(session)
    large_count = counter.count

    # 18 tables should not issue meaningfully more queries than 3 -- if this endpoint
    # regresses back to a per-table query loop, large_count would scale roughly linearly
    # with table count (order of ~90 vs ~15 queries) instead of staying flat.
    assert large_count <= small_count + 2
    assert large_count < 15


def test_sla_status_reports_maintenance_recency_and_p95_latency_per_table(
    postgres_session,
) -> None:
    session = postgres_session
    source = _seed_source(session)
    now = datetime.now(UTC)

    fresh = _seed_table(session, source, scan_at=now - timedelta(hours=1))
    stale = _seed_table(session, source, scan_at=now - timedelta(hours=1))

    session.add(
        AuditLog(
            table_id=fresh.id,
            ts=now - timedelta(days=1),
            action="optimize",
            status="succeeded",
        )
    )
    session.add(
        AuditLog(
            table_id=stale.id,
            ts=now - timedelta(days=40),
            action="optimize",
            status="succeeded",
        )
    )
    for latency in (100, 200, 900):
        session.add(
            QueryLog(
                source_id=source.id,
                table_id=fresh.id,
                query_id=f"q-{uuid.uuid4()}",
                engine="trino",
                executed_at=now - timedelta(hours=1),
                duration_ms=latency,
            )
        )
    session.flush()

    result = _call(session)
    by_table = {row.table_id: row for row in result.slas}

    fresh_row = by_table[fresh.id]
    assert fresh_row.maintenance_days is not None
    assert 0.9 < fresh_row.maintenance_days < 1.1
    assert fresh_row.p95_latency_ms is not None
    assert fresh_row.p95_latency_ms > 200  # near the top of [100, 200, 900]

    stale_row = by_table[stale.id]
    assert stale_row.maintenance_days is not None
    assert stale_row.maintenance_days > 30
    assert stale_row.maintenance_breached is True
    assert stale_row.p95_latency_ms is None  # no QueryLog rows seeded for this table


def _isolate_preset_resolution(session: Session) -> None:
    # A migrated database already holds an is_default preset (20260929_1100) and may hold
    # rules from other tests, and both steer effective_preset(). Neutralize them inside the
    # fixture's rolled-back transaction so each test seeds exactly the coverage it asserts.
    session.execute(update(PresetRule).values(enabled=False))
    session.execute(update(Preset).values(is_default=False))
    session.flush()


def _seed_preset(session: Session, **kwargs: object) -> Preset:
    destination = Destination(
        id=uuid.uuid4(), name=f"dest-{uuid.uuid4()}", engine="native", config={}
    )
    session.add(destination)
    session.flush()
    preset = Preset(
        id=uuid.uuid4(),
        name=f"preset-{uuid.uuid4()}",
        destination_id=destination.id,
        mode="standard",
        operations=["optimize"],
        **kwargs,
    )
    session.add(preset)
    session.flush()
    return preset


def test_sla_status_reports_preset_provenance_per_table(postgres_session) -> None:
    # Tables with no assignment and no matching rule fall through to the tenant's
    # is_default preset (cryo/scheduler/rules.py::effective_preset), so has_preset alone
    # cannot tell an explicitly covered table from one only caught by the fallback.
    session = postgres_session
    _isolate_preset_resolution(session)
    source = _seed_source(session)
    now = datetime.now(UTC)
    rule_schema = f"rule_{uuid.uuid4().hex[:8]}"

    assigned = _seed_table(session, source, scan_at=now - timedelta(hours=1), schema_name="lms")
    ruled = _seed_table(session, source, scan_at=now - timedelta(hours=1), schema_name=rule_schema)
    defaulted = _seed_table(
        session, source, scan_at=now - timedelta(hours=1), schema_name="revenue"
    )

    assigned_preset = _seed_preset(session)
    session.add(TableAssignment(table_id=assigned.id, preset_id=assigned_preset.id, pinned=True))
    rule_preset = _seed_preset(session)
    session.add(
        PresetRule(
            preset_id=rule_preset.id,
            predicate={"field": "schema_name", "op": "=", "value": rule_schema},
            enabled=True,
        )
    )
    _seed_preset(session, is_default=True, guardrails={"sla_maintenance_days": 3})
    session.flush()

    by_table = {row.table_id: row for row in _call(session).slas}

    assigned_row = by_table[assigned.id]
    assert assigned_row.schema_name == "lms"
    assert assigned_row.has_preset is True
    assert assigned_row.preset_provenance == "assignment"

    ruled_row = by_table[ruled.id]
    assert ruled_row.has_preset is True
    assert ruled_row.preset_provenance == "rule"

    defaulted_row = by_table[defaulted.id]
    assert defaulted_row.schema_name == "revenue"
    assert defaulted_row.has_preset is True
    assert defaulted_row.preset_provenance == "default"
    assert defaulted_row.maintenance_sla_days == 3


def test_sla_status_reports_no_preset_when_no_default_exists(postgres_session) -> None:
    # Without an is_default preset, a table with no assignment or matching rule resolves to
    # nothing: its maintenance SLA can never be met, which is what has_preset=False surfaces.
    session = postgres_session
    _isolate_preset_resolution(session)
    source = _seed_source(session)
    now = datetime.now(UTC)

    uncovered = _seed_table(
        session, source, scan_at=now - timedelta(hours=1), schema_name="revenue"
    )

    uncovered_row = {row.table_id: row for row in _call(session).slas}[uncovered.id]
    assert uncovered_row.has_preset is False
    assert uncovered_row.preset_provenance == "none"
