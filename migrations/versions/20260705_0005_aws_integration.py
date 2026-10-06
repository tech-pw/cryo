"""merge scanner_config/multi_tenant branches and add aws_integrations

The migration history had branched into two independent heads (20260704_0010 "scanner_config"
and 20260705_0004 "multi_tenant", both descending from 20260703_0009) — this revision merges
them back into a single head and adds the aws_integrations table in the same step.

Revision ID: 20260705_0005
Revises: 20260704_0010, 20260705_0004
Create Date: 2026-07-05

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260705_0005"
down_revision = ("20260704_0010", "20260705_0004")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aws_integrations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("access_key_id", sa.Text(), nullable=True),
        sa.Column("secret_ref", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_tested_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
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
        sa.ForeignKeyConstraint(["source_id"], ["cryo.sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_table("aws_integrations", schema="cryo")
