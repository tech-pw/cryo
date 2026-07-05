"""add abac policies table

Revision ID: 20260705_0002
Revises: 20260705_0001
Create Date: 2026-07-05 11:22:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260705_0002"
down_revision = "20260705_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create abac_policies table
    op.create_table(
        "abac_policies",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("row_filter", sa.Text(), nullable=True),
        sa.Column("column_masks", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "allowed_operations",
            postgresql.JSONB(),
            nullable=False,
            server_default='["SELECT"]',
        ),
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
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["tables.id"],
            name=op.f("fk_abac_policies_table_id_tables"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_abac_policies")),
    )
    
    # Create indexes for policy lookups
    op.create_index(
        op.f("ix_abac_policies_table_id"),
        "abac_policies",
        ["table_id"],
    )
    op.create_index(
        op.f("ix_abac_policies_role"),
        "abac_policies",
        ["role"],
    )
    
    # Create unique constraint for table+role combination
    op.create_index(
        op.f("ix_abac_policies_unique"),
        "abac_policies",
        ["table_id", "role"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_abac_policies_unique"), table_name="abac_policies")
    op.drop_index(op.f("ix_abac_policies_role"), table_name="abac_policies")
    op.drop_index(op.f("ix_abac_policies_table_id"), table_name="abac_policies")
    op.drop_table("abac_policies")
