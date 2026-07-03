"""add cost tables and summary columns

Revision ID: 20260703_0002
Revises: 20260630_0001
Create Date: 2026-07-03 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260703_0002"
down_revision = "20260630_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "table_stats",
        sa.Column("projected_monthly_s3_cost_usd", sa.Float(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_stats",
        sa.Column("projected_savings_usd_month", sa.Float(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_stats",
        sa.Column("reclaimable_bytes", sa.BigInteger(), nullable=True),
        schema="cryo",
    )

    op.add_column(
        "audit_log",
        sa.Column("actual_bytes_freed", sa.BigInteger(), nullable=True),
        schema="cryo",
    )

    op.create_table(
        "table_cost",
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("precision", sa.Text(), nullable=False),
        sa.Column("price_per_gb_month", sa.Float(), nullable=False),
        sa.Column("live_bytes", sa.BigInteger(), nullable=False),
        sa.Column("dead_bytes", sa.BigInteger(), nullable=False),
        sa.Column("metadata_bytes", sa.BigInteger(), nullable=False),
        sa.Column("orphan_bytes", sa.BigInteger(), nullable=True),
        sa.Column("listed_bytes", sa.BigInteger(), nullable=True),
        sa.Column("last_full_cost_scan_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "reclaim_expire_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "reclaim_orphan_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "reclaim_optimize_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "reclaim_manifests_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("current_monthly_cost_usd", sa.Float(), nullable=False),
        sa.Column("projected_monthly_cost_usd", sa.Float(), nullable=False),
        sa.Column("projected_savings_usd_month", sa.Float(), nullable=False),
        sa.Column(
            "query_request_savings_usd_month",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("table_id"),
        schema="cryo",
    )

    op.create_table(
        "table_cost_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("current_monthly_cost_usd", sa.Float(), nullable=True),
        sa.Column("projected_savings_usd_month", sa.Float(), nullable=True),
        sa.Column("reclaimable_bytes", sa.BigInteger(), nullable=True),
        sa.Column("precision", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )
    op.create_index(
        "ix_table_cost_history_table_scan",
        "table_cost_history",
        ["table_id", "scan_at"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_table_cost_history_table_scan",
        table_name="table_cost_history",
        schema="cryo",
    )
    op.drop_table("table_cost_history", schema="cryo")
    op.drop_table("table_cost", schema="cryo")
    op.drop_column("audit_log", "actual_bytes_freed", schema="cryo")
    op.drop_column("table_stats", "reclaimable_bytes", schema="cryo")
    op.drop_column("table_stats", "projected_savings_usd_month", schema="cryo")
    op.drop_column("table_stats", "projected_monthly_s3_cost_usd", schema="cryo")
