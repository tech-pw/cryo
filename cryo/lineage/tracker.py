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
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import TIMESTAMP

from cryo.db.base import Base
from cryo.db.models import TimestampMixin


class LineageEdge(TimestampMixin, Base):
    """A lineage edge representing a data flow between tables."""

    __tablename__ = "lineage_edges"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_table_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tables.id"), nullable=False)
    target_table_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tables.id"), nullable=False)
    edge_type: Mapped[str] = mapped_column(Text, nullable=False)  # "read", "write", "transform"
    job_name: Mapped[str | None] = mapped_column(Text)  # Spark job, Airflow DAG, etc.
    job_id: Mapped[str | None] = mapped_column(Text)
    query_pattern: Mapped[str | None] = mapped_column(Text)  # Simplified query pattern
    last_observed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    observation_count: Mapped[int] = mapped_column(default=1)  # How many times this edge was observed
    metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


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


def parse_query_for_lineage(
    query_text: str,
    target_table_id: UUID,
) -> list[tuple[str, str]]:  # List of (schema, table_name)
    """
    Parse a query to extract source tables for lineage.
    
    This is a simplified parser. In production, use sqlglot or similar.
    
    Args:
        query_text: SQL query text
        target_table_id: Target table being written to
    
    Returns:
        List of (schema, table_name) tuples
    """
    import re
    
    # Simple regex to find "FROM schema.table" patterns
    # This is very basic - production should use proper SQL parser
    from_pattern = r'\bFROM\s+([a-z0-9_]+)\.([a-z0-9_]+)'
    join_pattern = r'\bJOIN\s+([a-z0-9_]+)\.([a-z0-9_]+)'
    
    tables = []
    
    query_lower = query_text.lower()
    
    for match in re.finditer(from_pattern, query_lower):
        schema, table = match.groups()
        tables.append((schema, table))
    
    for match in re.finditer(join_pattern, query_lower):
        schema, table = match.groups()
        tables.append((schema, table))
    
    return list(set(tables))  # Deduplicate


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
        Lineage graph
    """
    from cryo.db.models import Table
    
    table = session.get(Table, table_id)
    if not table:
        raise ValueError(f"Table {table_id} not found")
    
    table_name = f"{table.catalog}.{table.schema_name}.{table.table_name}"
    
    # Get upstream tables (sources)
    upstream_stmt = (
        select(LineageEdge, Table)
        .join(Table, LineageEdge.source_table_id == Table.id)
        .where(LineageEdge.target_table_id == table_id)
        .order_by(LineageEdge.last_observed_at.desc())
    )
    
    upstream_results = session.execute(upstream_stmt).all()
    upstream_tables = [
        LineageNode(
            table_id=source_table.id,
            table_name=f"{source_table.catalog}.{source_table.schema_name}.{source_table.table_name}",
            edge_type=edge.edge_type,
            job_name=edge.job_name,
            last_observed_at=edge.last_observed_at,
        )
        for edge, source_table in upstream_results
    ]
    
    # Get downstream tables (consumers)
    downstream_stmt = (
        select(LineageEdge, Table)
        .join(Table, LineageEdge.target_table_id == Table.id)
        .where(LineageEdge.source_table_id == table_id)
        .order_by(LineageEdge.last_observed_at.desc())
    )
    
    downstream_results = session.execute(downstream_stmt).all()
    downstream_tables = [
        LineageNode(
            table_id=target_table.id,
            table_name=f"{target_table.catalog}.{target_table.schema_name}.{target_table.table_name}",
            edge_type=edge.edge_type,
            job_name=edge.job_name,
            last_observed_at=edge.last_observed_at,
        )
        for edge, target_table in downstream_results
    ]
    
    # Collect unique job names
    jobs = set()
    for edge, _ in upstream_results + downstream_results:
        if edge.job_name:
            jobs.add(edge.job_name)
    
    return LineageGraph(
        table_id=table_id,
        table_name=table_name,
        upstream_tables=upstream_tables,
        downstream_tables=downstream_tables,
        jobs=sorted(jobs),
    )
