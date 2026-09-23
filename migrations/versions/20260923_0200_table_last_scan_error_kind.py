"""Add cryo.tables.last_scan_error_kind.

Separates "this table's metadata is gone from object storage" from an ordinary scan failure.
A 387-table scan of cdp.central on 2026-09-23 reported 201 failures, all of them the former:
the Iceberg manifest list named by the table's own metadata returns NoSuchKey from S3, and
Trino fails identically on the same tables. Retrying those forever inflates the failure rate
with a number that cannot improve, so they are recorded distinctly and can be listed.

Nullable and additive: existing rows keep NULL until their next scan attempt classifies them.

Revision ID: 20260923_0200
Revises: 20260922_2100
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_0200"
down_revision = "20260922_2100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tables",
        sa.Column("last_scan_error_kind", sa.Text(), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("tables", "last_scan_error_kind", schema="cryo")
