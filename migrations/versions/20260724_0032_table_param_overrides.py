"""Add param_overrides to table_assignments so a specific table can tweak
maintenance params (e.g. a tighter retention_days) without forking the
shared preset, which may be pinned to other tables that shouldn't be
affected by the change.

Revision ID: 20260724_0032
Revises: 20260722_0031
Create Date: 2026-07-24 09:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260724_0032"
down_revision = "20260722_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "table_assignments",
        sa.Column(
            "param_overrides",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("table_assignments", "param_overrides", schema="cryo")
