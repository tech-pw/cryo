"""give assistant tables the uuid default their models assume

Revision ID: 20260930_1800
Revises: 20260930_1600
Create Date: 2026-09-30 18:00:00.000000

20260928_1810 created the assistant tables with `id uuid PRIMARY KEY` and no default, while
the models declare `server_default=gen_random_uuid()` and never send an id. Tests build the
schema with Base.metadata.create_all(), so they got the default and passed; production, built
by this migration chain, did not. Every "Ask Cryo" message has failed since with
`NotNullViolation: null value in column "id" of relation "assistant_conversations"`.

Only the column default changes: no data is rewritten. assistant_evidence (id supplied by the
caller) and assistant_settings (keyed by tenant_id) are not affected.
"""

from __future__ import annotations

from alembic import op

revision = "20260930_1800"
down_revision = "20260930_1600"
branch_labels = None
depends_on = None

TABLES = (
    "assistant_conversations",
    "assistant_messages",
    "assistant_tool_calls",
    "assistant_action_proposals",
    "assistant_authorizations",
    "assistant_feedback",
    "assistant_api_keys",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE cryo.{table} ALTER COLUMN id SET DEFAULT gen_random_uuid()")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE cryo.{table} ALTER COLUMN id DROP DEFAULT")
