"""Add maintenance_schema_names to sources — optional allowlist of Iceberg namespaces Cryo may scan and maintain."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260706_0010"
down_revision = "20260706_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "maintenance_schema_names",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("sources", "maintenance_schema_names", schema="cryo")
