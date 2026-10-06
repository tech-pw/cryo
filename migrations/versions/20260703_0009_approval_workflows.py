"""approval workflows for destructive operations

Revision ID: 20260703_0009
Revises: 20260703_0008
Create Date: 2026-07-03 23:30:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260703_0009"
down_revision = "20260703_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create approval_requests table
    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_type", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("request_reason", sa.Text(), nullable=True),
        sa.Column("approval_reason", sa.Text(), nullable=True),
        sa.Column("impact_preview", postgresql.JSONB(), nullable=False),
        sa.Column("operation_params", postgresql.JSONB(), nullable=False),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("executed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("execution_job_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            ["table_id"], ["cryo.tables.id"], name=op.f("fk_approval_requests_table_id_tables")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
        schema="cryo",
    )

    # Create index on status for pending approvals
    op.create_index(
        op.f("ix_approval_requests_status"),
        "approval_requests",
        ["status"],
        schema="cryo",
    )

    # Create index on table_id for filtering
    op.create_index(
        op.f("ix_approval_requests_table_id"),
        "approval_requests",
        ["table_id"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_approval_requests_table_id"), table_name="approval_requests", schema="cryo")
    op.drop_index(op.f("ix_approval_requests_status"), table_name="approval_requests", schema="cryo")
    op.drop_table("approval_requests", schema="cryo")
