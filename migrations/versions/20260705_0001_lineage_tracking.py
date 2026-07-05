"""add lineage tracking tables

Revision ID: 20260705_0001
Revises: 20260703_0009
Create Date: 2026-07-05 11:21:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260705_0001"
down_revision = "20260703_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create lineage_edges table
    op.create_table(
        "lineage_edges",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("source_table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edge_type", sa.Text(), nullable=False),
        sa.Column("job_name", sa.Text(), nullable=True),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("query_pattern", sa.Text(), nullable=True),
        sa.Column("last_observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["source_table_id"],
            ["tables.id"],
            name=op.f("fk_lineage_edges_source_table_id_tables"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_table_id"],
            ["tables.id"],
            name=op.f("fk_lineage_edges_target_table_id_tables"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lineage_edges")),
    )
    
    # Create indexes for lineage queries
    op.create_index(
        op.f("ix_lineage_edges_source_table_id"),
        "lineage_edges",
        ["source_table_id"],
    )
    op.create_index(
        op.f("ix_lineage_edges_target_table_id"),
        "lineage_edges",
        ["target_table_id"],
    )
    op.create_index(
        op.f("ix_lineage_edges_edge_type"),
        "lineage_edges",
        ["edge_type"],
    )
    
    # Create unique constraint to prevent duplicate edges
    op.create_index(
        op.f("ix_lineage_edges_unique"),
        "lineage_edges",
        ["source_table_id", "target_table_id", "edge_type"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_lineage_edges_unique"), table_name="lineage_edges")
    op.drop_index(op.f("ix_lineage_edges_edge_type"), table_name="lineage_edges")
    op.drop_index(op.f("ix_lineage_edges_target_table_id"), table_name="lineage_edges")
    op.drop_index(op.f("ix_lineage_edges_source_table_id"), table_name="lineage_edges")
    op.drop_table("lineage_edges")
