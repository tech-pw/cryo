"""Add maintenance_missing_metadata_reason/maintenance_retry_after to tables.

Revision ID: 20260920_0054
Revises: 20260920_0053
Create Date: 2026-09-20 00:00:05.000000

Critical vitals action plan, 2026-09-20, P1 "contain repeated NoSuchKey work safely": persisted,
reversible containment for a table whose registered location's metadata.json/manifest is
confirmed missing in S3 (cryo.engines.base.is_table_not_found_error) but doesn't fit
deactivate_orphaned_evolving_catalog_entries' narrow location-IS-NULL heuristic -- see
cryo/db/models.py::Table's new columns' docstring.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_0054"
down_revision = "20260920_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tables",
        sa.Column("maintenance_missing_metadata_reason", sa.Text(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "tables",
        sa.Column("maintenance_retry_after", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("tables", "maintenance_retry_after", schema="cryo")
    op.drop_column("tables", "maintenance_missing_metadata_reason", schema="cryo")
