"""local users and UI-managed Google sign-in

Revision ID: 20260930_1900
Revises: 20260930_1800
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260930_1900"
down_revision = "20260930_1800"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), server_default=sa.text("'viewer'"), nullable=False),
        sa.Column("disabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_local_users"),
        sa.UniqueConstraint("username", name="uq_local_users_username"),
        schema="cryo",
    )
    op.create_table(
        "auth_settings",
        sa.Column("id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "password_login_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("google_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("google_client_id", sa.Text(), nullable=True),
        sa.Column("google_hosted_domain", sa.Text(), nullable=True),
        sa.Column(
            "google_auto_provision", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_settings"),
        sa.CheckConstraint("id = 1", name="ck_auth_settings_singleton"),
        schema="cryo",
    )
    op.execute("INSERT INTO cryo.auth_settings (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table("auth_settings", schema="cryo")
    op.drop_table("local_users", schema="cryo")
