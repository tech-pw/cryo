"""Premortem hardening: bandit state + embedding cache tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260705_0006"
down_revision = "20260705_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "governor_bandit_state",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
        schema="cryo",
    )
    op.create_table(
        "table_embeddings",
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stats_fingerprint", sa.Text(), nullable=False),
        sa.Column("embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("table_id"),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_table("table_embeddings", schema="cryo")
    op.drop_table("governor_bandit_state", schema="cryo")
