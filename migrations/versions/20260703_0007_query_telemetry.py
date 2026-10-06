"""Add query telemetry and layout optimization tables.

Revision ID: 20260703_0007
Revises: 20260703_0006
Create Date: 2026-07-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260703_0007"
down_revision: Union[str, None] = "20260703_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Query logs - raw query entries from engines
    op.create_table(
        "query_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("table_id", sa.UUID(), nullable=True),
        sa.Column("query_id", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column("query_type", sa.Text(), nullable=True),
        sa.Column("filter_columns", postgresql.JSONB(), nullable=True),
        sa.Column("join_columns", postgresql.JSONB(), nullable=True),
        sa.Column("projection_columns", postgresql.JSONB(), nullable=True),
        sa.Column("executed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("rows_read", sa.BigInteger(), nullable=True),
        sa.Column("bytes_read", sa.BigInteger(), nullable=True),
        sa.Column("files_read", sa.Integer(), nullable=True),
        sa.Column("files_pruned", sa.Integer(), nullable=True),
        sa.Column("user_name", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["cryo.sources.id"]),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )
    op.create_index("ix_query_logs_table_id", "query_logs", ["table_id"], schema="cryo")
    op.create_index("ix_query_logs_executed_at", "query_logs", ["executed_at"], schema="cryo")

    # Query patterns - aggregated patterns per table/column
    op.create_table(
        "query_patterns",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("table_id", sa.UUID(), nullable=False),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("filter_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("equality_filter_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("range_filter_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("join_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("projection_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("avg_selectivity", sa.Float(), nullable=True),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("table_id", "column_name", name="uq_query_patterns_table_column"),
        schema="cryo",
    )
    op.create_index("ix_query_patterns_table_id", "query_patterns", ["table_id"], schema="cryo")

    # Layout recommendations - sort/cluster recommendations per table
    op.create_table(
        "layout_recommendations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("table_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("current_sort_order", postgresql.JSONB(), nullable=True),
        sa.Column("recommended_strategy", sa.Text(), nullable=False),
        sa.Column("recommended_columns", postgresql.JSONB(), nullable=False),
        sa.Column("estimated_prune_improvement", sa.Float(), nullable=True),
        sa.Column("current_prune_efficiency", sa.Float(), nullable=True),
        sa.Column("analysis_query_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("confidence", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("bloom_filter_columns", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_table("layout_recommendations", schema="cryo")
    op.drop_index("ix_query_patterns_table_id", table_name="query_patterns", schema="cryo")
    op.drop_table("query_patterns", schema="cryo")
    op.drop_index("ix_query_logs_executed_at", table_name="query_logs", schema="cryo")
    op.drop_index("ix_query_logs_table_id", table_name="query_logs", schema="cryo")
    op.drop_table("query_logs", schema="cryo")
