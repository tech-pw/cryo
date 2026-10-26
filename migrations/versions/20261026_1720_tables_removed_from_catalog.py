"""tables.removed_from_catalog_at / removed_reason tombstone (#120)

Revision ID: 20261026_1720
Revises: 20261026_1710
Create Date: 2026-10-26 17:20:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261026_1720"
down_revision = "20261026_1710"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tables",
        sa.Column("removed_from_catalog_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )
    op.add_column("tables", sa.Column("removed_reason", sa.Text(), nullable=True), schema="cryo")
    op.create_index(
        "ix_cryo_tables_removed_from_catalog_at",
        "tables",
        ["removed_from_catalog_at"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_cryo_tables_removed_from_catalog_at", table_name="tables", schema="cryo")
    op.drop_column("tables", "removed_reason", schema="cryo")
    op.drop_column("tables", "removed_from_catalog_at", schema="cryo")
