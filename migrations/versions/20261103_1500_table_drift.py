"""Per-table drift accumulator for continuous maintenance (#77)

Revision ID: 20261103_1500
Revises: 20261026_1330

The commit watcher reads each new snapshot's summary and adds its file/byte counters here;
a successful maintenance job resets the row. One row per table, so the table stays as small
as the watched fleet.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261103_1500"
down_revision = "20261026_1330"
branch_labels = None
depends_on = None


def upgrade() -> None:
    zero = sa.text("0")
    op.create_table(
        "table_drift",
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("commits", sa.Integer(), server_default=zero, nullable=False),
        sa.Column("data_files_added", sa.BigInteger(), server_default=zero, nullable=False),
        sa.Column("small_files_added", sa.BigInteger(), server_default=zero, nullable=False),
        sa.Column("delete_files_added", sa.BigInteger(), server_default=zero, nullable=False),
        sa.Column("bytes_added", sa.BigInteger(), server_default=zero, nullable=False),
        sa.Column("maintenance_commits", sa.Integer(), server_default=zero, nullable=False),
        sa.Column("baseline_score", sa.Integer(), nullable=True),
        sa.Column("estimated_score", sa.Integer(), nullable=True),
        sa.Column("first_commit_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_commit_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("reset_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["cryo.tables.id"],
            name="fk_table_drift_table_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("table_id", name="pk_table_drift"),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_table("table_drift", schema="cryo")
