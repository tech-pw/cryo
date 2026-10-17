"""per-table maintenance operation exclusions

Revision ID: 20261017_1200
Revises: 20261016_1200
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261017_1200"
down_revision = "20261016_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "table_operation_exclusions",
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["cryo.tables.id"],
            name="fk_table_operation_exclusions_table_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("table_id", "operation", name="pk_table_operation_exclusions"),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_table("table_operation_exclusions", schema="cryo")
