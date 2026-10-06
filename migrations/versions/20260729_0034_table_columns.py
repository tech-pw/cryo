"""Add cryo.table_columns index for column-level catalog search.

Revision ID: 20260729_0034
Revises: 20260727_0033
Create Date: 2026-07-29 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260729_0034"
down_revision = "20260727_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "table_columns",
        sa.Column(
            "table_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("column_name", sa.Text(), primary_key=True, nullable=False),
        sa.Column("data_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="cryo",
    )
    op.create_index(
        "ix_table_columns_column_name",
        "table_columns",
        ["column_name"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_table_columns_column_name", table_name="table_columns", schema="cryo")
    op.drop_table("table_columns", schema="cryo")
