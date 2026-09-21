"""Tests for cryo.planner.trino_hot_tables.top_trino_query_tables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import QueryLog, Source, Table
from cryo.planner.trino_hot_tables import top_trino_query_tables

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(session, source: Source, *, name: str, is_active: bool = True) -> Table:
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="s",
        table_name=name,
        is_active=is_active,
    )
    session.add(table)
    session.flush()
    return table


def _log_query(
    session,
    *,
    source: Source,
    table: Table,
    engine: str,
    when: datetime,
) -> None:
    session.add(
        QueryLog(
            source_id=source.id,
            table_id=table.id,
            query_id=f"q-{uuid4()}",
            engine=engine,
            executed_at=when,
        )
    )


def test_ranks_tables_by_trino_query_count_descending(session):
    source = _seed_source(session)
    hot = _seed_table(session, source, name="hot")
    warm = _seed_table(session, source, name="warm")
    cold = _seed_table(session, source, name="cold")

    for _ in range(10):
        _log_query(session, source=source, table=hot, engine="trino", when=NOW - timedelta(days=1))
    for _ in range(3):
        _log_query(session, source=source, table=warm, engine="trino", when=NOW - timedelta(days=1))
    _log_query(session, source=source, table=cold, engine="trino", when=NOW - timedelta(days=1))
    session.commit()

    ranked = top_trino_query_tables(session, limit=10, now=NOW)

    assert [r.table_id for r in ranked] == [hot.id, warm.id, cold.id]
    assert ranked[0].trino_query_count == 10
    assert ranked[1].trino_query_count == 3
    assert ranked[2].trino_query_count == 1


def test_excludes_non_trino_engine_queries(session):
    """A table queried heavily on Spark but never on Trino has zero Trino cost to reduce --
    must not appear in a Trino-cost-reduction ranking."""
    source = _seed_source(session)
    spark_only = _seed_table(session, source, name="spark_only")
    trino_table = _seed_table(session, source, name="trino_table")

    for _ in range(50):
        _log_query(session, source=source, table=spark_only, engine="spark", when=NOW - timedelta(days=1))
    _log_query(session, source=source, table=trino_table, engine="trino", when=NOW - timedelta(days=1))
    session.commit()

    ranked = top_trino_query_tables(session, limit=10, now=NOW)

    assert [r.table_id for r in ranked] == [trino_table.id]


def test_excludes_queries_outside_the_window(session):
    source = _seed_source(session)
    table = _seed_table(session, source, name="t")

    _log_query(session, source=source, table=table, engine="trino", when=NOW - timedelta(days=45))
    session.commit()

    ranked = top_trino_query_tables(session, limit=10, window_days=30, now=NOW)

    assert ranked == []


def test_excludes_inactive_tables(session):
    source = _seed_source(session)
    inactive = _seed_table(session, source, name="gone", is_active=False)

    _log_query(session, source=source, table=inactive, engine="trino", when=NOW - timedelta(days=1))
    session.commit()

    ranked = top_trino_query_tables(session, limit=10, now=NOW)

    assert ranked == []


def test_respects_limit(session):
    source = _seed_source(session)
    tables = [_seed_table(session, source, name=f"t{i}") for i in range(5)]
    for table in tables:
        _log_query(session, source=source, table=table, engine="trino", when=NOW - timedelta(days=1))
    session.commit()

    ranked = top_trino_query_tables(session, limit=2, now=NOW)

    assert len(ranked) == 2


def test_tie_breaks_by_table_id_for_stable_ordering(session):
    """A daily repin job must produce a stable, reproducible set for tables tied on count --
    otherwise the pinned set could flap between equally-hot tables from run to run."""
    source = _seed_source(session)
    a = _seed_table(session, source, name="a")
    b = _seed_table(session, source, name="b")
    for table in (a, b):
        _log_query(session, source=source, table=table, engine="trino", when=NOW - timedelta(days=1))
    session.commit()

    first = top_trino_query_tables(session, limit=10, now=NOW)
    second = top_trino_query_tables(session, limit=10, now=NOW)

    assert [r.table_id for r in first] == [r.table_id for r in second]
    assert [r.table_id for r in first] == sorted(t.id for t in (a, b))
