"""add multi-tenant support

Revision ID: 20260705_0004
Revises: 20260705_0003
Create Date: 2026-07-05 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260705_0004"
down_revision = "20260705_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("admin_user_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("slug"),
        schema="cryo",
    )

    for table_name in ("sources", "destinations", "tables", "presets"):
        op.add_column(
            table_name,
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
            schema="cryo",
        )
        op.create_index(
            op.f(f"ix_{table_name}_tenant_id"),
            table_name,
            ["tenant_id"],
            schema="cryo",
        )
        op.create_foreign_key(
            op.f(f"fk_{table_name}_tenant_id_tenants"),
            table_name,
            "tenants",
            ["tenant_id"],
            ["id"],
            source_schema="cryo",
            referent_schema="cryo",
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table_name in ("presets", "tables", "destinations", "sources"):
        op.drop_constraint(
            op.f(f"fk_{table_name}_tenant_id_tenants"),
            table_name,
            schema="cryo",
            type_="foreignkey",
        )
        op.drop_index(op.f(f"ix_{table_name}_tenant_id"), table_name=table_name, schema="cryo")
        op.drop_column(table_name, "tenant_id", schema="cryo")

    op.drop_table("tenants", schema="cryo")
