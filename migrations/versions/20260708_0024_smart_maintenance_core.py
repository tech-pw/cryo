"""Smart maintenance core schema additions.

Revision ID: 20260708_0024_smart_maintenance_core
Revises: 20260708_chunk_checkpointing
Create Date: 2026-07-08 19:45:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260708_0024_smart_maintenance_core"
down_revision = "20260708_chunk_checkpointing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("table_stats", sa.Column("equality_delete_files", sa.Integer(), nullable=True), schema="cryo")
    op.add_column("table_stats", sa.Column("position_delete_files", sa.Integer(), nullable=True), schema="cryo")
    op.add_column("table_stats", sa.Column("detection_tier", sa.Text(), nullable=True), schema="cryo")
    op.add_column("table_stats", sa.Column("snapshot_watermark", sa.BigInteger(), nullable=True), schema="cryo")
    op.add_column(
        "table_stats",
        sa.Column("last_full_reconcile_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )

    op.add_column("layout_recommendations", sa.Column("approval_note", sa.Text(), nullable=True), schema="cryo")
    op.add_column("layout_recommendations", sa.Column("approved_by", sa.Text(), nullable=True), schema="cryo")
    op.add_column(
        "layout_recommendations",
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )
    op.add_column("layout_recommendations", sa.Column("dismissed_by", sa.Text(), nullable=True), schema="cryo")
    op.add_column(
        "layout_recommendations",
        sa.Column("dismissed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "layout_recommendations",
        sa.Column("dismissed_reason", sa.Text(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "layout_recommendations",
        sa.Column("applied_run_id", sa.dialects.postgresql.UUID(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "layout_recommendations",
        sa.Column("baseline_scan_bytes", sa.BigInteger(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "layout_recommendations",
        sa.Column("post_apply_scan_bytes", sa.BigInteger(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "layout_recommendations",
        sa.Column("observed_prune_improvement", sa.Float(), nullable=True),
        schema="cryo",
    )
    op.create_foreign_key(
        "fk_layout_recommendations_applied_run_id",
        "layout_recommendations",
        "runs",
        ["applied_run_id"],
        ["id"],
        source_schema="cryo",
        referent_schema="cryo",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_layout_recommendations_applied_run_id",
        "layout_recommendations",
        schema="cryo",
        type_="foreignkey",
    )
    op.drop_column("layout_recommendations", "observed_prune_improvement", schema="cryo")
    op.drop_column("layout_recommendations", "post_apply_scan_bytes", schema="cryo")
    op.drop_column("layout_recommendations", "baseline_scan_bytes", schema="cryo")
    op.drop_column("layout_recommendations", "applied_run_id", schema="cryo")
    op.drop_column("layout_recommendations", "dismissed_reason", schema="cryo")
    op.drop_column("layout_recommendations", "dismissed_at", schema="cryo")
    op.drop_column("layout_recommendations", "dismissed_by", schema="cryo")
    op.drop_column("layout_recommendations", "approved_at", schema="cryo")
    op.drop_column("layout_recommendations", "approved_by", schema="cryo")
    op.drop_column("layout_recommendations", "approval_note", schema="cryo")

    op.drop_column("table_stats", "last_full_reconcile_at", schema="cryo")
    op.drop_column("table_stats", "snapshot_watermark", schema="cryo")
    op.drop_column("table_stats", "detection_tier", schema="cryo")
    op.drop_column("table_stats", "position_delete_files", schema="cryo")
    op.drop_column("table_stats", "equality_delete_files", schema="cryo")
