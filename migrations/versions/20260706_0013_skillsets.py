"""Add skillsets + skillset_outcomes tables for AI skillset versioning (paper arXiv:2606.01185)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260706_0013"
down_revision = "20260706_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skillsets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("prompts", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("tools", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("metrics", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("name", "version", name="uq_skillsets_name_version"),
        sa.UniqueConstraint("name", name="uq_skillsets_name"),
        schema="cryo",
    )
    op.create_index("ix_skillsets_name", "skillsets", ["name"], unique=True, schema="cryo")

    op.create_table(
        "skillset_outcomes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "skillset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.skillsets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "table_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id"),
            nullable=True,
        ),
        sa.Column("prompt_name", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        schema="cryo",
    )
    op.create_index(
        "ix_skillset_outcomes_skillset_recorded",
        "skillset_outcomes",
        ["skillset_id", "recorded_at"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_skillset_outcomes_skillset_recorded", table_name="skillset_outcomes", schema="cryo")
    op.drop_table("skillset_outcomes", schema="cryo")
    op.drop_index("ix_skillsets_name", table_name="skillsets", schema="cryo")
    op.drop_table("skillsets", schema="cryo")