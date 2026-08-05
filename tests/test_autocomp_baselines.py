from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import Source, Table, TablePartition
from cryo.planner.benefit_score import compare_scheduling_strategies


def test_autocomp_report_includes_deterministic_baselines_and_regret(session: Session) -> None:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name="events",
        is_active=True,
    )
    session.add_all([source, table])
    session.flush()
    now = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    for index in range(12):
        session.add(
            TablePartition(
                table_id=table.id,
                scan_at=now,
                partition_value=f"day={index:02d}",
                partition_values={"day": index},
                partition_fields=[],
                partition_size_gb=float(12 - index),
                small_files_in_partition_count=index + 1,
                compaction_benefit_score=float((index * 7) % 13),
            )
        )
    session.commit()

    first = compare_scheduling_strategies(session, table.id)
    second = compare_scheduling_strategies(session, table.id)

    assert first == second
    assert [baseline["name"] for baseline in first["baselines"]] == [
        "size_only",
        "file_count",
        "threshold",
        "fifo",
        "random",
    ]
    assert all(baseline["regret"] >= 0 for baseline in first["baselines"])
    assert first["confidence"] == 1.0
    assert first["evidence_sufficient"] is True
    assert first["static_order"] == first["baselines"][1]["order"]


def test_autocomp_report_marks_sparse_unscored_evidence_insufficient(session: Session) -> None:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name="sparse_events",
        is_active=True,
    )
    session.add_all([source, table])
    session.flush()
    session.add(
        TablePartition(
            table_id=table.id,
            scan_at=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
            partition_value="day=01",
            partition_values={"day": 1},
            partition_fields=[],
            partition_size_gb=1.0,
            small_files_in_partition_count=20,
            compaction_benefit_score=None,
        )
    )
    session.commit()

    report = compare_scheduling_strategies(session, table.id)

    assert report["confidence"] == 0.0
    assert report["evidence_sufficient"] is False
    assert report["autocomp_wins"] is False
