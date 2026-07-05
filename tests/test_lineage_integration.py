"""Integration tests for data lineage tracking."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from cryo.db.models import Source, Table
from cryo.lineage.tracker import (
    LineageEdge,
    get_lineage_graph,
    parse_query_for_lineage,
    record_lineage_from_query,
)


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={"catalog_name": "lake"},
    )
    session.add(source)
    session.flush()
    return source


def _seed_table(session, *, schema_name: str, table_name: str) -> Table:
    source = _seed_source(session)
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name=schema_name,
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.flush()
    return table


def test_parse_query_for_lineage_extracts_source_tables():
    """parse_query_for_lineage should find tables referenced in FROM/JOIN clauses."""
    target_id = uuid4()
    query = """
        INSERT INTO warehouse.facts.orders
        SELECT o.id, c.name
        FROM raw.customers c
        JOIN raw.orders o ON o.customer_id = c.id
    """

    tables = parse_query_for_lineage(query, target_id)

    assert ("raw", "customers") in tables
    assert ("raw", "orders") in tables


def test_record_lineage_from_query_persists_edges(session):
    """record_lineage_from_query should create lineage edges for known source tables."""
    source_table = _seed_table(session, schema_name="raw", table_name="customers")
    target_table = _seed_table(session, schema_name="warehouse", table_name="dim_customers")

    query = "INSERT INTO warehouse.dim_customers SELECT * FROM raw.customers"

    edges_recorded = record_lineage_from_query(
        session,
        query,
        target_table.id,
        job_name="dim_load",
        job_id="job-42",
    )

    assert edges_recorded >= 1

    edge = session.scalar(
        select(LineageEdge).where(
            LineageEdge.source_table_id == source_table.id,
            LineageEdge.target_table_id == target_table.id,
        )
    )
    assert edge is not None
    assert edge.job_name == "dim_load"
    assert edge.job_id == "job-42"
    assert edge.edge_type == "read"


def test_record_lineage_from_query_increments_observation_count(session):
    """Repeated observations should bump observation_count on existing edges."""
    _seed_table(session, schema_name="raw", table_name="events")
    target_table = _seed_table(session, schema_name="mart", table_name="daily_events")
    query = "INSERT INTO mart.daily_events SELECT * FROM raw.events"

    record_lineage_from_query(session, query, target_table.id)
    record_lineage_from_query(session, query, target_table.id)

    edge = session.scalar(select(LineageEdge).where(LineageEdge.target_table_id == target_table.id))
    assert edge is not None
    assert edge.observation_count == 2


def test_get_lineage_graph_returns_upstream_and_downstream(session):
    """get_lineage_graph should traverse edges in both directions."""
    upstream = _seed_table(session, schema_name="raw", table_name="orders")
    middle = _seed_table(session, schema_name="staging", table_name="orders_clean")
    downstream = _seed_table(session, schema_name="mart", table_name="order_facts")

    now = datetime.now(UTC)
    session.add_all(
        [
            LineageEdge(
                source_table_id=upstream.id,
                target_table_id=middle.id,
                edge_type="read",
                job_name="clean_orders",
                last_observed_at=now,
            ),
            LineageEdge(
                source_table_id=middle.id,
                target_table_id=downstream.id,
                edge_type="read",
                job_name="build_facts",
                last_observed_at=now,
            ),
        ]
    )
    session.commit()

    graph = get_lineage_graph(session, middle.id, depth=2)

    assert graph.table_id == middle.id
    assert len(graph.upstream_tables) == 1
    assert graph.upstream_tables[0].table_id == upstream.id
    assert len(graph.downstream_tables) == 1
    assert graph.downstream_tables[0].table_id == downstream.id
    assert "clean_orders" in graph.jobs
    assert "build_facts" in graph.jobs
