"""Add observation_archive_meta + plan_verifier job columns.

Combines Feature A (Observation Lakehouse, SANER 2026 / arXiv:2512.02795) and Feature B
(Plan Verifier, arXiv:2510.09567). Both changes are additive and independent so they ship in a
single Alembic revision.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260706_0014"
down_revision = "20260706_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Observation Lakehouse archive metadata -- tracks the last compaction so /observability/stats
    # and the cron compactor can show when it last ran without re-walking every file.
    op.create_table(
        "observation_archive_meta",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("archive_path", sa.Text(), nullable=False),
        sa.Column("last_compact_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_compact_files_merged", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("last_compact_rows", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="cryo",
    )

    # Plan Verifier -- persisted on jobs so the worker can refuse to execute a job whose plan
    # was rejected at enqueue time (verified=false), and so audit/UI can show the verdict hash.
    op.add_column(
        "jobs",
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema="cryo",
    )
    op.add_column(
        "jobs",
        sa.Column("plan_hash", sa.Text(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "jobs",
        sa.Column("verifier_version", sa.Text(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "jobs",
        sa.Column(
            "verifier_violations",
            JSONB(),
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("jobs", "verifier_violations", schema="cryo")
    op.drop_column("jobs", "verifier_version", schema="cryo")
    op.drop_column("jobs", "plan_hash", schema="cryo")
    op.drop_column("jobs", "verified", schema="cryo")
    op.drop_table("observation_archive_meta", schema="cryo")