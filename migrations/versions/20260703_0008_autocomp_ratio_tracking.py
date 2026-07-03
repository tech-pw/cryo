"""Add AutoComp benefit score, compaction effectiveness ratio, and Flink engine support.

Revision ID: 20260703_0008
Revises: 20260703_0007
Create Date: 2026-07-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260703_0008"
down_revision: Union[str, None] = "20260703_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AutoComp benefit scoring on table_partitions
    op.add_column(
        "table_partitions",
        sa.Column("query_frequency", sa.Float(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_partitions",
        sa.Column("compaction_benefit_score", sa.Float(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_partitions",
        sa.Column("last_compacted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_partitions",
        sa.Column("input_files_last_compaction", sa.Integer(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_partitions",
        sa.Column("output_files_last_compaction", sa.Integer(), nullable=True),
        schema="cryo",
    )

    # Compaction effectiveness ratio on audit_log
    op.add_column(
        "audit_log",
        sa.Column("input_files_count", sa.Integer(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "audit_log",
        sa.Column("output_files_count", sa.Integer(), nullable=True),
        schema="cryo",
    )

    # Index for benefit score queries (descending — highest priority first)
    op.create_index(
        "ix_table_partitions_benefit_score",
        "table_partitions",
        [sa.text("compaction_benefit_score DESC NULLS LAST")],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_table_partitions_benefit_score", table_name="table_partitions", schema="cryo")
    op.drop_column("audit_log", "output_files_count", schema="cryo")
    op.drop_column("audit_log", "input_files_count", schema="cryo")
    op.drop_column("table_partitions", "output_files_last_compaction", schema="cryo")
    op.drop_column("table_partitions", "input_files_last_compaction", schema="cryo")
    op.drop_column("table_partitions", "last_compacted_at", schema="cryo")
    op.drop_column("table_partitions", "compaction_benefit_score", schema="cryo")
    op.drop_column("table_partitions", "query_frequency", schema="cryo")
