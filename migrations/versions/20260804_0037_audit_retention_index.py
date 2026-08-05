"""Index audit timestamps for bounded retention sweeps.

Revision ID: 20260804_0037
Revises: 20260804_0036
Create Date: 2026-08-04 15:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "20260804_0037"
down_revision = "20260804_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"], schema="cryo")


def downgrade() -> None:
    op.drop_index("ix_audit_log_ts", table_name="audit_log", schema="cryo")
