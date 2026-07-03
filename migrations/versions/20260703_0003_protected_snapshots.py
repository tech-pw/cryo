"""protected snapshots and stats history raw payload

Revision ID: 20260703_0003
Revises: 20260703_0002
Create Date: 2026-07-03 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260703_0003"
down_revision = "20260703_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "table_stats_history",
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="cryo",
    )
    op.create_table(
        "protected_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("protected_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )
    op.create_index(
        "ix_protected_snapshots_table",
        "protected_snapshots",
        ["table_id", "snapshot_id"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_protected_snapshots_table", table_name="protected_snapshots", schema="cryo")
    op.drop_table("protected_snapshots", schema="cryo")
    op.drop_column("table_stats_history", "raw", schema="cryo")
