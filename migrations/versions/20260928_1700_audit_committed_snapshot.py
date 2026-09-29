"""Store the snapshot a maintenance action committed, as a decimal string.

Iceberg snapshot ids do not fit in a JavaScript number. The column stays null
until an engine reports committed_snapshot_id in its procedure metrics.

Revision ID: 20260928_1700
Revises: 20260928_1300
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260928_1700"
down_revision = "20260928_1300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("committed_snapshot_id", sa.Text(), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("audit_log", "committed_snapshot_id", schema="cryo")
