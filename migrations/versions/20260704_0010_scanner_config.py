"""Add scanner configuration.

Revision ID: 20260704_0010
Revises: 20260703_0009
Create Date: 2026-07-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260704_0010"
down_revision: Union[str, None] = "20260703_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scanner_config",
        sa.Column("id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "schedule_cron",
            sa.Text(),
            server_default=sa.text("'0 2 * * *'"),
            nullable=False,
        ),
        sa.Column("timezone", sa.Text(), server_default=sa.text("'UTC'"), nullable=False),
        sa.Column("num_chunks", sa.Integer(), server_default=sa.text("4"), nullable=False),
        sa.Column(
            "full_scan_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "cost_full_scan_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "max_concurrent_runs",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_scanner_config_singleton"),
        sa.CheckConstraint("num_chunks > 0", name="ck_scanner_config_num_chunks_positive"),
        sa.CheckConstraint(
            "max_concurrent_runs > 0",
            name="ck_scanner_config_max_concurrent_runs_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )
    op.execute(
        """
        INSERT INTO cryo.scanner_config (
            id, enabled, schedule_cron, timezone, num_chunks,
            full_scan_default, cost_full_scan_default, max_concurrent_runs
        )
        VALUES (1, true, '0 2 * * *', 'UTC', 4, false, false, 1)
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("scanner_config", schema="cryo")
