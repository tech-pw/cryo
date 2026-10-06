"""Create partition_access_daily for the partition-access-visualization feature.

Revision ID: 20260819_0046
Revises: 20260819_0045

docs/PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md -- inspired by Yelp's engineering blog
post on partition access visualizations (S3 access logs -> partition-key-vs-time scatter plot
-> storage-class/retention decisions). See cryo.db.models.PartitionAccessDaily's docstring for
why this is a separate table from s3_request_cost_daily rather than an added column there.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260819_0046"
down_revision = "20260819_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "partition_access_daily",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("table_id", UUID(as_uuid=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("partition_path", sa.Text(), nullable=False),
        sa.Column("requester_role", sa.Text(), nullable=True),
        sa.Column(
            "request_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["cryo.tables.id"],
            name="fk_partition_access_daily_table_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_partition_access_daily"),
        sa.UniqueConstraint(
            "table_id",
            "day",
            "partition_path",
            "requester_role",
            name="uq_partition_access_daily_row",
        ),
        schema="cryo",
    )
    op.create_index(
        "ix_partition_access_daily_table_day",
        "partition_access_daily",
        ["table_id", "day"],
        schema="cryo",
    )
    op.create_index(
        "ix_partition_access_daily_table_partition",
        "partition_access_daily",
        ["table_id", "partition_path"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_partition_access_daily_table_partition",
        table_name="partition_access_daily",
        schema="cryo",
    )
    op.drop_index(
        "ix_partition_access_daily_table_day",
        table_name="partition_access_daily",
        schema="cryo",
    )
    op.drop_table("partition_access_daily", schema="cryo")
