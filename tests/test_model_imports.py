from __future__ import annotations

from sqlalchemy.dialects.postgresql import JSONB

from cryo.db.base import Base
from cryo.db.models import TablePartition


def test_all_foundation_tables_are_registered() -> None:
    assert Base.metadata.schema == "cryo"
    assert set(Base.metadata.tables) == {
        "cryo.audit_log",
        "cryo.destinations",
        "cryo.jobs",
        "cryo.preset_rules",
        "cryo.presets",
        "cryo.runs",
        "cryo.sources",
        "cryo.table_assignments",
        "cryo.table_cost",
        "cryo.table_cost_history",
        "cryo.table_partitions",
        "cryo.table_stats",
        "cryo.table_stats_history",
        "cryo.tables",
    }


def test_partition_model_keeps_multi_field_metadata() -> None:
    columns = TablePartition.__table__.c

    assert isinstance(columns.partition_values.type, JSONB)
    assert isinstance(columns.partition_fields.type, JSONB)
    assert isinstance(columns.raw.type, JSONB)
