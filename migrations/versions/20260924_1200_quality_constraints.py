"""Add cryo.quality_constraints and cryo.quality_constraint_results.

Declarative data-quality constraints (Deequ-style rules over stored scan metrics, ZSDQ §5.4)
and their per-scan evaluations. Additive only.

Revision ID: 20260924_1200
Revises: 20260923_1500
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260924_1200"
down_revision = "20260923_1500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quality_constraints",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "table_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=True),
        sa.Column("column_name", sa.Text(), nullable=True),
        sa.Column(
            "params",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("severity", sa.Text(), nullable=False, server_default=sa.text("'error'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("table_id", "name", name="uq_quality_constraints_table_id"),
        schema="cryo",
    )
    op.create_index(
        "ix_quality_constraints_table_id",
        "quality_constraints",
        ["table_id"],
        schema="cryo",
    )
    op.create_table(
        "quality_constraint_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "constraint_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.quality_constraints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "table_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evaluated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("scanned_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "observed",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        schema="cryo",
    )
    op.create_index(
        "ix_quality_constraint_results_constraint_id",
        "quality_constraint_results",
        ["constraint_id", "evaluated_at"],
        schema="cryo",
    )
    op.create_index(
        "ix_quality_constraint_results_table_id",
        "quality_constraint_results",
        ["table_id"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_table("quality_constraint_results", schema="cryo")
    op.drop_table("quality_constraints", schema="cryo")
