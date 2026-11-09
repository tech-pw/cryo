"""Record what expire_snapshots removed on the audit row (#434)

Revision ID: 20261109_1200
Revises: 20261103_1200

Additive and nullable: NULL means the engine did not report a count.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261109_1200"
down_revision = "20261103_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_log", sa.Column("objects_deleted", sa.BigInteger(), nullable=True), schema="cryo"
    )
    op.add_column(
        "audit_log", sa.Column("snapshots_expired", sa.Integer(), nullable=True), schema="cryo"
    )
    op.add_column(
        "audit_log",
        sa.Column("result_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("audit_log", "result_metrics", schema="cryo")
    op.drop_column("audit_log", "snapshots_expired", schema="cryo")
    op.drop_column("audit_log", "objects_deleted", schema="cryo")
