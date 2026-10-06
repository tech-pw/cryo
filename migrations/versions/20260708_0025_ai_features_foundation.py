"""AI features foundation: skillset versioning fix + LLM observations + run diagnosis.

Revision ID: 20260708_0025
Revises: 20260708_chunk_checkpointing, 20260708_0024
Create Date: 2026-07-08 21:40:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260708_0025"
down_revision = ("20260708_chunk_checkpointing", "20260708_0024")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Skillsets should support multiple versions under one name.
    op.drop_constraint("uq_skillsets_name", "skillsets", schema="cryo", type_="unique")
    op.drop_index("ix_skillsets_name", table_name="skillsets", schema="cryo")
    op.create_index("ix_skillsets_name", "skillsets", ["name"], unique=False, schema="cryo")

    op.create_table(
        "llm_observations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("provider_id", UUID(as_uuid=True), sa.ForeignKey("cryo.ai_providers.id"), nullable=True),
        sa.Column("provider_type", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("feature", sa.Text(), nullable=False),
        sa.Column("prompt_name", sa.Text(), nullable=True),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("skillset_id", UUID(as_uuid=True), sa.ForeignKey("cryo.skillsets.id"), nullable=True),
        sa.Column("skillset_name", sa.Text(), nullable=True),
        sa.Column("skillset_version", sa.Text(), nullable=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("cryo.runs.id"), nullable=True),
        sa.Column("table_id", UUID(as_uuid=True), sa.ForeignKey("cryo.tables.id"), nullable=True),
        sa.Column(
            "outcome_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.skillset_outcomes.id"),
            nullable=True,
        ),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'success'")),
        sa.Column("budget_denied", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("extra", JSONB(), nullable=True),
        schema="cryo",
    )
    op.create_index(
        "ix_llm_observations_recorded_at",
        "llm_observations",
        ["recorded_at"],
        schema="cryo",
    )
    op.create_index(
        "ix_llm_observations_provider_day",
        "llm_observations",
        ["provider_type", "recorded_at"],
        schema="cryo",
    )
    op.create_index(
        "ix_llm_observations_skillset",
        "llm_observations",
        ["skillset_id", "recorded_at"],
        schema="cryo",
    )

    op.create_table(
        "run_failure_diagnoses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("cryo.jobs.id"), nullable=True),
        sa.Column("table_id", UUID(as_uuid=True), sa.ForeignKey("cryo.tables.id"), nullable=True),
        sa.Column("skillset_id", UUID(as_uuid=True), sa.ForeignKey("cryo.skillsets.id"), nullable=True),
        sa.Column("diagnosis_class", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("runbook_step", sa.Text(), nullable=True),
        sa.Column("evidence", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("raw_response", JSONB(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("feedback_note", sa.Text(), nullable=True),
        sa.Column("diagnosed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("feedback_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )
    op.create_index(
        "ix_run_failure_diagnoses_run",
        "run_failure_diagnoses",
        ["run_id", "diagnosed_at"],
        schema="cryo",
    )
    op.create_index(
        "ix_run_failure_diagnoses_job",
        "run_failure_diagnoses",
        ["job_id", "diagnosed_at"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_run_failure_diagnoses_job", table_name="run_failure_diagnoses", schema="cryo")
    op.drop_index("ix_run_failure_diagnoses_run", table_name="run_failure_diagnoses", schema="cryo")
    op.drop_table("run_failure_diagnoses", schema="cryo")

    op.drop_index("ix_llm_observations_skillset", table_name="llm_observations", schema="cryo")
    op.drop_index("ix_llm_observations_provider_day", table_name="llm_observations", schema="cryo")
    op.drop_index("ix_llm_observations_recorded_at", table_name="llm_observations", schema="cryo")
    op.drop_table("llm_observations", schema="cryo")

    op.drop_index("ix_skillsets_name", table_name="skillsets", schema="cryo")
    op.create_index("ix_skillsets_name", "skillsets", ["name"], unique=True, schema="cryo")
    op.create_unique_constraint("uq_skillsets_name", "skillsets", ["name"], schema="cryo")
