"""add performance indexes and constraints

Revision ID: 20260705_0003
Revises: 20260705_0002
Create Date: 2026-07-05 11:23:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260705_0003"
down_revision = "20260705_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add indexes for query performance dashboard
    op.create_index(
        op.f("ix_query_logs_executed_at"),
        "query_logs",
        ["executed_at"],
    )
    op.create_index(
        op.f("ix_query_logs_engine"),
        "query_logs",
        ["engine"],
    )
    op.create_index(
        op.f("ix_query_logs_table_id"),
        "query_logs",
        ["table_id"],
    )
    
    # Add GIN index for JSONB query on tables_accessed
    op.execute(
        "CREATE INDEX ix_query_logs_tables_accessed_gin "
        "ON query_logs USING gin (tables_accessed)"
    )
    
    # Add indexes for table stats history (health trends)
    op.create_index(
        op.f("ix_table_stats_history_table_id_scan_at"),
        "table_stats_history",
        ["table_id", "scan_at"],
    )
    op.create_index(
        op.f("ix_table_stats_history_scan_at"),
        "table_stats_history",
        ["scan_at"],
    )
    
    # Add index for audit logs (job detail page)
    op.create_index(
        op.f("ix_audit_logs_job_id"),
        "audit_logs",
        ["job_id"],
    )
    op.create_index(
        op.f("ix_audit_logs_table_id_started_at"),
        "audit_logs",
        ["table_id", "started_at"],
    )
    
    # Add index for query patterns (partition evolution)
    op.create_index(
        op.f("ix_query_patterns_table_id_query_count"),
        "query_patterns",
        ["table_id", "query_count"],
    )
    
    # Add benefit_score column to table_partitions (AutoComp)
    op.add_column(
        "table_partitions",
        sa.Column("compaction_benefit_score", sa.Float(), nullable=True),
    )
    op.create_index(
        op.f("ix_table_partitions_benefit_score"),
        "table_partitions",
        ["compaction_benefit_score"],
    )
    
    # Add query_frequency column for AutoComp benefit scoring
    op.add_column(
        "table_partitions",
        sa.Column("query_frequency", sa.Integer(), nullable=True, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("table_partitions", "query_frequency")
    op.drop_index(op.f("ix_table_partitions_benefit_score"), table_name="table_partitions")
    op.drop_column("table_partitions", "compaction_benefit_score")
    
    op.drop_index(op.f("ix_query_patterns_table_id_query_count"), table_name="query_patterns")
    op.drop_index(op.f("ix_audit_logs_table_id_started_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_job_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_table_stats_history_scan_at"), table_name="table_stats_history")
    op.drop_index(op.f("ix_table_stats_history_table_id_scan_at"), table_name="table_stats_history")
    
    op.execute("DROP INDEX ix_query_logs_tables_accessed_gin")
    
    op.drop_index(op.f("ix_query_logs_table_id"), table_name="query_logs")
    op.drop_index(op.f("ix_query_logs_engine"), table_name="query_logs")
    op.drop_index(op.f("ix_query_logs_executed_at"), table_name="query_logs")
