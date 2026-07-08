"""deletion_tracking table for GDPR/CCPA compliance.

Move 19 from docs/strategy-next-moves-2026-07.md: persistent record of
rows pending hard deletion. The existing compliance layer exposed
"pending_deletion" derived from a transient stats dict. This migration
adds a durable table so soft-delete events survive restarts and so a
fleet-wide \"N pending hard deletions, blocked until expiry after <date>\"
report is queryable.

Revision ID: 20260708_0023
Revises: 20260708_chunk_checkpointing
Create Date: 2026-07-08 17:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260708_0023"
down_revision = "20260708_chunk_checkpointing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deletion_tracking",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "table_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("soft_deleted_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "pending_rows", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("blocked_until", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )
    op.create_index(
        "ix_deletion_tracking_table",
        "deletion_tracking",
        ["table_id"],
        schema="cryo",
    )
    op.create_index(
        "ix_deletion_tracking_resolved",
        "deletion_tracking",
        ["resolved_at"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_deletion_tracking_resolved", table_name="deletion_tracking", schema="cryo")
    op.drop_index("ix_deletion_tracking_table", table_name="deletion_tracking", schema="cryo")
    op.drop_table("deletion_tracking", schema="cryo")
