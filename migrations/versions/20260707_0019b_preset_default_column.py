"""Add presets.is_default column

Revision ID: 20260707_0019b_preset_default
Revises: 20260707_0019
Create Date: 2026-07-07 20:30:00.000000

`cryo/db/models.py`'s `Preset.is_default` column (nullable=False, server_default=false)
was added to the ORM model without a migration to actually create it in the database --
`20260707_0020_unset_native_default_preset.py` (a later migration) assumes this column
already exists and runs `UPDATE cryo.presets SET is_default = false WHERE is_default = true`
against it, which fails with `UndefinedColumn` on any environment that runs migrations in
order from a clean state (confirmed: reproduces identically with zero involvement from any
other branch's changes -- this is a gap in that migration's own prerequisites, not a
conflict with other work).

This migration adds the missing column, chained between the merge point (20260707_0019)
and 20260707_0020 so the existing migration's assumption becomes true by the time it runs.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260707_0019b_preset_default"
down_revision = "20260707_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "presets",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("presets", "is_default", schema="cryo")
