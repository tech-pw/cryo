"""Ask Cryo conversations, evidence, proposals and usage columns.

Revision ID: 20260928_1810
Revises: 20260928_1700
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260928_1810"
down_revision = "20260928_1700"
branch_labels = None
depends_on = None

SCHEMA = "cryo"


def upgrade() -> None:
    op.create_table(
        "assistant_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_subject", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("share_token_hash", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_assistant_conversations_owner_updated",
        "assistant_conversations",
        ["owner_subject", "updated_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "assistant_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("blocks", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'streaming'")),
        sa.Column("verified_handler", sa.Text(), nullable=True),
        sa.Column("grounding", sa.Text(), nullable=True),
        sa.Column("client_message_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["cryo.assistant_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "conversation_id", "client_message_id", name="uq_assistant_messages_client"
        ),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_assistant_messages_seq"),
        schema=SCHEMA,
    )
    op.create_table(
        "assistant_tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error_category", sa.Text(), nullable=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["message_id"], ["cryo.assistant_messages.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_table(
        "assistant_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("scope", postgresql.JSONB(), nullable=True),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "retrieved_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("coverage", postgresql.JSONB(), nullable=True),
        sa.Column("source_refs", postgresql.JSONB(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("result_digest", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tool_call_id"], ["cryo.assistant_tool_calls.id"], ondelete="SET NULL"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "assistant_action_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_subject", sa.Text(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column(
            "arguments", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("preview", postgresql.JSONB(), nullable=True),
        sa.Column("plan_hash", sa.Text(), nullable=True),
        sa.Column("target_digest", sa.Text(), nullable=True),
        sa.Column("selection", postgresql.JSONB(), nullable=True),
        sa.Column("required_role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_ids", postgresql.JSONB(), nullable=True),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["cryo.assistant_conversations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["cryo.assistant_messages.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["cryo.runs.id"]),
        sa.UniqueConstraint("idempotency_key"),
        schema=SCHEMA,
    )
    op.create_table(
        "assistant_authorizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_subject", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column(
            "authorized_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["cryo.assistant_action_proposals.id"], ondelete="CASCADE"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "assistant_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_subject", sa.Text(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["message_id"], ["cryo.assistant_messages.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_table(
        "assistant_settings",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "allowed_min_role", sa.Text(), nullable=False, server_default=sa.text("'viewer'")
        ),
        sa.Column(
            "enabled_tiers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[0]'::jsonb"),
        ),
        sa.Column("send_row_values", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default=sa.text("90")),
        sa.Column("team_instructions_skillset", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "assistant_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[\"read\"]'::jsonb"),
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("key_hash"),
        schema=SCHEMA,
    )
    op.add_column(
        "llm_observations",
        sa.Column("principal_subject", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "llm_observations",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column("audit_log", sa.Column("origin", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "audit_log",
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("audit_log", "proposal_id", schema=SCHEMA)
    op.drop_column("audit_log", "origin", schema=SCHEMA)
    op.drop_column("llm_observations", "conversation_id", schema=SCHEMA)
    op.drop_column("llm_observations", "principal_subject", schema=SCHEMA)
    op.drop_table("assistant_api_keys", schema=SCHEMA)
    op.drop_table("assistant_settings", schema=SCHEMA)
    op.drop_table("assistant_feedback", schema=SCHEMA)
    op.drop_table("assistant_authorizations", schema=SCHEMA)
    op.drop_table("assistant_action_proposals", schema=SCHEMA)
    op.drop_table("assistant_evidence", schema=SCHEMA)
    op.drop_table("assistant_tool_calls", schema=SCHEMA)
    op.drop_table("assistant_messages", schema=SCHEMA)
    op.drop_table("assistant_conversations", schema=SCHEMA)
