"""Per-table lifecycle: active or archived (#519)

Revision ID: 20261112_1200
Revises: 20261109_1200

Additive. Every existing table is active. lifecycle_changed_* record the latest change;
the audit log keeps the full history.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261112_1200"
down_revision = "20261109_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tables",
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        schema="cryo",
    )
    op.add_column(
        "tables",
        sa.Column("lifecycle_changed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "tables", sa.Column("lifecycle_changed_by", sa.Text(), nullable=True), schema="cryo"
    )
    op.add_column("tables", sa.Column("lifecycle_reason", sa.Text(), nullable=True), schema="cryo")


def downgrade() -> None:
    op.drop_column("tables", "lifecycle_reason", schema="cryo")
    op.drop_column("tables", "lifecycle_changed_by", schema="cryo")
    op.drop_column("tables", "lifecycle_changed_at", schema="cryo")
    op.drop_column("tables", "lifecycle", schema="cryo")
