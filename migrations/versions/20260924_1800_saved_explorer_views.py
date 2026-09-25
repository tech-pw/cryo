"""Add cryo.saved_explorer_views.

Named catalog filters owned by a principal. Additive only. No schedule.

Revision ID: 20260924_1800
Revises: 20260924_1200
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260924_1800"
down_revision = "20260924_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_explorer_views",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_subject", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "filters",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("sort", sa.Text(), nullable=True),
        sa.Column("group_by", sa.Text(), nullable=True),
        sa.Column(
            "savings_view",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
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
        sa.UniqueConstraint("owner_subject", "name", name="uq_saved_explorer_views_owner_name"),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_table("saved_explorer_views", schema="cryo")
