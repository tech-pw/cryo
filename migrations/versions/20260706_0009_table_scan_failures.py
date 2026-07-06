"""Track consecutive scan failures per table so persistently-broken tables (e.g. stale dbt
__dbt_tmp pointers with a deleted metadata.json) can be surfaced distinctly from a table that
scans fine but needs maintenance."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260706_0009"
down_revision = "20260706_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tables",
        sa.Column(
            "consecutive_scan_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema="cryo",
    )
    op.add_column(
        "tables",
        sa.Column("last_scan_error", sa.Text(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "tables",
        sa.Column("last_scan_error_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("tables", "last_scan_error_at", schema="cryo")
    op.drop_column("tables", "last_scan_error", schema="cryo")
    op.drop_column("tables", "consecutive_scan_failures", schema="cryo")
