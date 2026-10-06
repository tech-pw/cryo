"""table owner + alert subscriptions/dispatch history

Revision ID: 20260707_0017
Revises: 20260706_0016
Create Date: 2026-07-07 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260707_0017"
down_revision = "20260706_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tables",
        sa.Column("owner_email", sa.Text(), nullable=True),
        schema="cryo",
    )
    op.create_table(
        "table_alert_subscriptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("table_id", "email"),
        schema="cryo",
    )
    op.create_table(
        "table_alert_dispatches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "recipients",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "sent_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )
    op.create_index(
        "ix_table_alert_dispatches_table",
        "table_alert_dispatches",
        ["table_id", "sent_at"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_table_alert_dispatches_table", table_name="table_alert_dispatches", schema="cryo")
    op.drop_table("table_alert_dispatches", schema="cryo")
    op.drop_table("table_alert_subscriptions", schema="cryo")
    op.drop_column("tables", "owner_email", schema="cryo")
