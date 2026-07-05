from __future__ import annotations

from sqlalchemy.dialects.postgresql import JSONB

from cryo.db.base import Base
from cryo.db.models import TablePartition
from cryo.lineage.tracker import LineageEdge
from cryo.security.abac import ABACPolicy
from cryo.workflows.approvals import ApprovalRequest

FOUNDATION_TABLES = {
    "cryo.ai_providers",
    "cryo.audit_log",
    "cryo.destinations",
    "cryo.jobs",
    "cryo.layout_recommendations",
    "cryo.preset_rules",
    "cryo.presets",
    "cryo.protected_snapshots",
    "cryo.query_logs",
    "cryo.query_patterns",
    "cryo.runs",
    "cryo.scanner_config",
    "cryo.slack_configs",
    "cryo.sources",
    "cryo.table_assignments",
    "cryo.table_cost",
    "cryo.table_cost_history",
    "cryo.table_partitions",
    "cryo.table_stats",
    "cryo.table_stats_history",
    "cryo.tables",
    "cryo.tier_policies",
}

EXTENSION_TABLES = {
    "cryo.abac_policies",
    "cryo.approval_requests",
    "cryo.lineage_edges",
}


def test_all_foundation_tables_are_registered() -> None:
    assert Base.metadata.schema == "cryo"
    registered = set(Base.metadata.tables)
    assert FOUNDATION_TABLES <= registered
    assert EXTENSION_TABLES <= registered
    # Ensure extension models are mapped
    assert ApprovalRequest.__tablename__ == "approval_requests"
    assert LineageEdge.__tablename__ == "lineage_edges"
    assert ABACPolicy.__tablename__ == "abac_policies"


def test_partition_model_keeps_multi_field_metadata() -> None:
    columns = TablePartition.__table__.c

    assert isinstance(columns.partition_values.type, JSONB)
    assert isinstance(columns.partition_fields.type, JSONB)
    assert isinstance(columns.raw.type, JSONB)
