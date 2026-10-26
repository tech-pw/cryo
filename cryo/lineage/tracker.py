"""Data lineage tracking and graph construction.

Tracks table-level and column-level lineage from query logs
and OpenLineage events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import TIMESTAMP

from cryo.db.base import Base
from cryo.db.model_utils import qualified_table_name
from cryo.db.models import TimestampMixin
from cryo.observability.logging import quiet_sqlglot_logger


class LineageEdge(TimestampMixin, Base):
    """A lineage edge representing a data flow between tables."""

    __tablename__ = "lineage_edges"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_table_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tables.id"), nullable=False
    )
    target_table_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tables.id"), nullable=False
    )
    edge_type: Mapped[str] = mapped_column(Text, nullable=False)  # "read", "write", "transform"
    job_name: Mapped[str | None] = mapped_column(Text)  # Spark job, Airflow DAG, etc.
    job_id: Mapped[str | None] = mapped_column(Text)
    query_pattern: Mapped[str | None] = mapped_column(Text)  # Simplified query pattern
    last_observed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    observation_count: Mapped[int] = mapped_column(
        default=1
    )  # How many times this edge was observed
    detail: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


@dataclass
class LineageGraph:
    """A lineage graph for a table."""

    table_id: UUID
    table_name: str
    upstream_tables: list[LineageNode]  # Tables that feed into this table
    downstream_tables: list[LineageNode]  # Tables that consume from this table
    jobs: list[str]  # Jobs that touch this table


@dataclass
class LineageNode:
    """A node in the lineage graph."""

    table_id: UUID
    table_name: str
    edge_type: str
    job_name: str | None
    last_observed_at: datetime


def _parse_tables_regex(query_text: str) -> list[tuple[str, str]]:
    """Extract source tables using regex (fallback parser)."""
    import re

    from_pattern = r"\bfrom\s+([a-z0-9_]+)\.([a-z0-9_]+)"
    join_pattern = r"\bjoin\s+([a-z0-9_]+)\.([a-z0-9_]+)"

    tables: list[tuple[str, str]] = []
    query_lower = query_text.lower()

    for match in re.finditer(from_pattern, query_lower):
        schema, table = match.groups()
        tables.append((schema, table))

    for match in re.finditer(join_pattern, query_lower):
        schema, table = match.groups()
        tables.append((schema, table))

    return list(set(tables))


def _parse_tables_sqlglot(query_text: str) -> list[tuple[str, str]]:
    """Extract source tables using sqlglot when available."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return []

    tables: list[tuple[str, str]] = []
    parsed = None
    quiet_sqlglot_logger()  # never let sqlglot log the statement text (#165)
    for dialect in ("trino", "spark", "hive", "postgres"):
        try:
            parsed = sqlglot.parse_one(query_text, read=dialect)
            break
        except Exception:
            continue

    if parsed is None:
        return []

    for table in parsed.find_all(exp.Table):
        schema = table.db or "default"
        name = table.name
        if name:
            tables.append((schema.lower(), name.lower()))

    return list(set(tables))


def parse_query_for_lineage(
    query_text: str,
    target_table_id: UUID,
) -> list[tuple[str, str]]:  # List of (schema, table_name)
    """
    Parse a query to extract source tables for lineage.

    Uses sqlglot when installed, otherwise falls back to regex.

    Args:
        query_text: SQL query text
        target_table_id: Target table being written to

    Returns:
        List of (schema, table_name) tuples
    """
    del target_table_id  # reserved for future write-target detection

    if not query_text.strip():
        return []

    tables = _parse_tables_sqlglot(query_text)
    if not tables:
        tables = _parse_tables_regex(query_text)

    return tables


def record_lineage_from_query(
    session: Session,
    query_text: str,
    target_table_id: UUID,
    job_name: str | None = None,
    job_id: str | None = None,
) -> int:
    """
    Record lineage edges from a query.

    Args:
        session: Database session
        query_text: SQL query text
        target_table_id: Table being written to
        job_name: Optional job name
        job_id: Optional job ID

    Returns:
        Number of lineage edges recorded
    """
    from cryo.db.models import Table

    # Parse query for source tables
    source_tables = parse_query_for_lineage(query_text, target_table_id)

    if not source_tables:
        return 0

    # Find matching table IDs
    edges_recorded = 0

    for schema, table_name in source_tables:
        # Find source table
        stmt = select(Table).where(
            Table.schema_name == schema,
            Table.table_name == table_name,
        )
        source_table = session.execute(stmt).scalar_one_or_none()

        if not source_table:
            continue

        # Check if edge already exists
        edge_stmt = select(LineageEdge).where(
            LineageEdge.source_table_id == source_table.id,
            LineageEdge.target_table_id == target_table_id,
        )
        existing_edge = session.execute(edge_stmt).scalar_one_or_none()

        if existing_edge:
            # Update existing edge
            existing_edge.last_observed_at = datetime.now(UTC)
            existing_edge.observation_count += 1
        else:
            # Create new edge
            edge = LineageEdge(
                source_table_id=source_table.id,
                target_table_id=target_table_id,
                edge_type="read",
                job_name=job_name,
                job_id=job_id,
                query_pattern=query_text[:200],  # Store first 200 chars
                last_observed_at=datetime.now(UTC),
            )
            session.add(edge)

        edges_recorded += 1

    session.commit()

    return edges_recorded


def get_lineage_graph(
    session: Session,
    table_id: UUID,
    depth: int = 1,
) -> LineageGraph:
    """
    Get lineage graph for a table.

    Args:
        session: Database session
        table_id: Table ID
        depth: Depth of lineage to fetch (1 = direct, 2 = 2-hop, etc.)

    Returns:
        Lineage graph with upstream and downstream tables
    """
    from cryo.db.models import Table

    table = session.get(Table, table_id)
    if not table:
        raise ValueError(f"Table {table_id} not found")

    table_name = qualified_table_name(table)
    upstream_tables: list[LineageNode] = []
    downstream_tables: list[LineageNode] = []
    jobs: set[str] = set()
    seen_upstream: set[UUID] = set()
    seen_downstream: set[UUID] = set()

    upstream_frontier = {table_id}
    downstream_frontier = {table_id}

    for hop in range(max(depth, 1)):
        if upstream_frontier:
            upstream_stmt = (
                select(LineageEdge, Table)
                .join(Table, LineageEdge.source_table_id == Table.id)
                .where(LineageEdge.target_table_id.in_(upstream_frontier))
                .order_by(LineageEdge.last_observed_at.desc())
            )
            upstream_results = session.execute(upstream_stmt).all()
            next_upstream: set[UUID] = set()

            for edge, source_table in upstream_results:
                if edge.job_name:
                    jobs.add(edge.job_name)
                if source_table.id in seen_upstream:
                    continue
                seen_upstream.add(source_table.id)
                upstream_tables.append(
                    LineageNode(
                        table_id=source_table.id,
                        table_name=qualified_table_name(source_table),
                        edge_type=edge.edge_type,
                        job_name=edge.job_name,
                        last_observed_at=edge.last_observed_at,
                    )
                )
                if hop + 1 < depth:
                    next_upstream.add(source_table.id)

            upstream_frontier = next_upstream

        if downstream_frontier:
            downstream_stmt = (
                select(LineageEdge, Table)
                .join(Table, LineageEdge.target_table_id == Table.id)
                .where(LineageEdge.source_table_id.in_(downstream_frontier))
                .order_by(LineageEdge.last_observed_at.desc())
            )
            downstream_results = session.execute(downstream_stmt).all()
            next_downstream: set[UUID] = set()

            for edge, target_table in downstream_results:
                if edge.job_name:
                    jobs.add(edge.job_name)
                if target_table.id in seen_downstream:
                    continue
                seen_downstream.add(target_table.id)
                downstream_tables.append(
                    LineageNode(
                        table_id=target_table.id,
                        table_name=qualified_table_name(target_table),
                        edge_type=edge.edge_type,
                        job_name=edge.job_name,
                        last_observed_at=edge.last_observed_at,
                    )
                )
                if hop + 1 < depth:
                    next_downstream.add(target_table.id)

            downstream_frontier = next_downstream

    return LineageGraph(
        table_id=table_id,
        table_name=table_name,
        upstream_tables=upstream_tables,
        downstream_tables=downstream_tables,
        jobs=sorted(jobs),
    )
