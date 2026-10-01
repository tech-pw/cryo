"""durable per-partition last-read day

Revision ID: 20261001_1400
Revises: 20260930_1900
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261001_1400"
down_revision = "20260930_1900"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "partition_last_access",
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("partition_path", sa.Text(), nullable=False),
        sa.Column("first_read_day", sa.Date(), nullable=False),
        sa.Column("last_read_day", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["cryo.tables.id"],
            name="fk_partition_last_access_table_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("table_id", "partition_path", name="pk_partition_last_access"),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_table("partition_last_access", schema="cryo")
