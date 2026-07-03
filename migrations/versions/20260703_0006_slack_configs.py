"""Add Slack configs table.

Revision ID: 20260703_0006
Revises: 20260703_0005
Create Date: 2026-07-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260703_0006"
down_revision: Union[str, None] = "20260703_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "slack_configs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("webhook_url_encrypted", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("daily_report_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("daily_report_cron", sa.Text(), server_default=sa.text("'0 9 * * *'"), nullable=False),
        sa.Column("daily_report_use_ai", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_table("slack_configs", schema="cryo")
