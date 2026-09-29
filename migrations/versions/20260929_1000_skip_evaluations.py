"""Add cryo.skip_evaluations for nightly selection outcomes.

One row per evaluated table per batch run: stage reached, reason code, run id.
Additive. Inserts are multi-row, not one statement per table.

Retention is 90 days. prune_skip_evaluations deletes older rows; do not delete
from a request path.

Revision ID: 20260929_1000
Revises: 20260928_1810
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260929_1000"
down_revision = "20260928_1810"
branch_labels = None
depends_on = None

SCHEMA = "cryo"


def upgrade() -> None:
    op.create_table(
        "skip_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "table_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evaluated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "table_id", name="uq_skip_evaluations_run_table"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_skip_evaluations_run_id",
        "skip_evaluations",
        ["run_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_skip_evaluations_table_evaluated",
        "skip_evaluations",
        ["table_id", "evaluated_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_skip_evaluations_evaluated_at",
        "skip_evaluations",
        ["evaluated_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_skip_evaluations_evaluated_at", table_name="skip_evaluations", schema=SCHEMA)
    op.drop_index(
        "ix_skip_evaluations_table_evaluated", table_name="skip_evaluations", schema=SCHEMA
    )
    op.drop_index("ix_skip_evaluations_run_id", table_name="skip_evaluations", schema=SCHEMA)
    op.drop_table("skip_evaluations", schema=SCHEMA)
