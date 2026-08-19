"""Persist shared-S3 path anomaly report snapshots.

Revision ID: 20260818_0042
Revises: 20260817_0041
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260818_0042"
down_revision = "20260817_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shared_s3_anomaly_reports",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "generated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("table_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("group_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "overlapping_table_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("skipped_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "sources_scanned",
            JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "source_errors",
            JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("groups", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_shared_s3_anomaly_reports"),
        schema="cryo",
    )
    op.create_index(
        "ix_shared_s3_anomaly_reports_generated_at",
        "shared_s3_anomaly_reports",
        ["generated_at"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shared_s3_anomaly_reports_generated_at",
        table_name="shared_s3_anomaly_reports",
        schema="cryo",
    )
    op.drop_table("shared_s3_anomaly_reports", schema="cryo")
