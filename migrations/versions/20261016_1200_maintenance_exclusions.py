"""operator schema/table exclusions from maintenance

Revision ID: 20261016_1200
Revises: 20261001_1400
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261016_1200"
down_revision = "20261001_1400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_exclusions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("schema_name", sa.Text(), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["cryo.sources.id"],
            name="fk_maintenance_exclusions_source_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_maintenance_exclusions"),
        schema="cryo",
    )
    op.create_index(
        "uq_maintenance_exclusions_target",
        "maintenance_exclusions",
        ["source_id", "schema_name", sa.text("coalesce(table_name, '')")],
        unique=True,
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_maintenance_exclusions_target", table_name="maintenance_exclusions", schema="cryo"
    )
    op.drop_table("maintenance_exclusions", schema="cryo")
