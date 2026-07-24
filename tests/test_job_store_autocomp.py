"""AutoComp (SIGMOD 2025) wiring: job_store.py is what actually threads compaction_benefit_score
/ autocomp_traits from TablePartition rows into (1) the planner's candidate dicts and (2) the
governor's per-job AdmissionJob.estimated_benefit_score/moop_score. Both were previously dead --
_latest_partitions never included the score fields at all, and _job_record only ever read static
preset params, never a live per-table signal -- so these are regression tests for real production
gaps, not just new-feature coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from cryo.db.models import (
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TablePartition,
)
from cryo.db.repositories.job_store import SQLJobStore


def _seed_table_with_job(session) -> tuple[Table, MaintenanceJob]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(
        id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={"host": "trino"}
    )
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name="events",
        is_active=True,
    )
    session.add_all([source, destination, table])
    session.flush()

    run = MaintenanceRun(id=uuid4(), kind="adhoc", trigger="api", status="queued", summary={})
    session.add(run)
    session.flush()

    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status="queued",
    )
    session.add(job)
    session.commit()
    return table, job


def test_get_table_data_includes_benefit_score_and_moop_traits(session):
    table, _job = _seed_table_with_job(session)
    scan_at = datetime.now(UTC)
    session.add(
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2025-05-02"},
            partition_fields=[],
            partition_value="2025-05-02",
            partition_size_gb=10.0,
            small_files_in_partition_count=100,
            scan_at=scan_at,
            compaction_benefit_score=42.5,
            autocomp_traits={"t1": 0.9, "t2": 0.1, "t3": 0.0, "t4": 0.5, "s_c": 0.75},
        )
    )
    session.commit()

    store = SQLJobStore(lambda: session)
    data = store.get_table_data(str(table.id))

    assert len(data.partitions) == 1
    row = data.partitions[0]
    assert row["compaction_benefit_score"] == 42.5
    assert row["autocomp_traits"]["s_c"] == 0.75


def test_get_job_injects_live_benefit_and_moop_score_into_params(session):
    """Without this, AdmissionJob.estimated_benefit_score/moop_score are always None in
    production (job.params only ever carried static preset config), so governor's
    min_benefit_score / min_moop_score gates never actually fired."""
    table, job = _seed_table_with_job(session)
    scan_at = datetime.now(UTC)
    session.add_all(
        [
            TablePartition(
                table_id=table.id,
                partition_values={"dt": "2025-05-02"},
                partition_fields=[],
                partition_value="2025-05-02",
                partition_size_gb=10.0,
                small_files_in_partition_count=100,
                scan_at=scan_at,
                compaction_benefit_score=10.0,
                autocomp_traits={"s_c": 0.3},
            ),
            TablePartition(
                table_id=table.id,
                partition_values={"dt": "2025-05-03"},
                partition_fields=[],
                partition_value="2025-05-03",
                partition_size_gb=5.0,
                small_files_in_partition_count=50,
                scan_at=scan_at,
                compaction_benefit_score=90.0,  # the table's best candidate
                autocomp_traits={"s_c": 0.8},
            ),
        ]
    )
    session.commit()

    store = SQLJobStore(lambda: session)
    record = store.get_job(str(job.id))

    # The table's single best (max) candidate proxies "is this table worth admitting" for a
    # job-level (not partition-level) admission gate.
    assert record.params["benefit_score"] == 90.0
    assert record.params["moop_score"] == 0.8


def test_get_job_does_not_override_an_explicit_preset_param(session):
    from cryo.db.models import Preset

    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(
        id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={"host": "trino"}
    )
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name="events",
        is_active=True,
    )
    session.add_all([source, destination, table])
    session.flush()

    preset = Preset(
        id=uuid4(),
        name=f"preset-{uuid4()}",
        destination_id=destination.id,
        operations=["optimize"],
        params={"benefit_score": 999.0},
        guardrails={},
    )
    session.add(preset)
    session.flush()

    run = MaintenanceRun(
        id=uuid4(), kind="adhoc", trigger="api", status="queued", preset_id=preset.id, summary={}
    )
    session.add(run)
    session.flush()

    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status="queued",
    )
    session.add(job)
    scan_at = datetime.now(UTC)
    session.add(
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2025-05-02"},
            partition_fields=[],
            partition_value="2025-05-02",
            partition_size_gb=10.0,
            small_files_in_partition_count=100,
            scan_at=scan_at,
            compaction_benefit_score=1.0,
        )
    )
    session.commit()

    store = SQLJobStore(lambda: session)
    record = store.get_job(str(job.id))

    assert record.params["benefit_score"] == 999.0


def test_get_job_applies_table_param_overrides_over_preset_params(session):
    """table_assignments.param_overrides lets one table tweak a param (e.g. a tighter
    retention_days) without forking the shared preset, which may be pinned to other
    tables that shouldn't be affected. Override must win over the preset default."""
    from cryo.db.models import Preset, TableAssignment

    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(
        id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={"host": "trino"}
    )
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name="events",
        is_active=True,
    )
    session.add_all([source, destination, table])
    session.flush()

    preset = Preset(
        id=uuid4(),
        name=f"preset-{uuid4()}",
        destination_id=destination.id,
        operations=["expire_snapshots"],
        params={"retention_days": 7, "min_small_files": 10},
        guardrails={},
    )
    session.add(preset)
    session.flush()

    session.add(
        TableAssignment(
            table_id=table.id,
            preset_id=preset.id,
            param_overrides={"retention_days": 1},
        )
    )

    run = MaintenanceRun(
        id=uuid4(), kind="adhoc", trigger="api", status="queued", preset_id=preset.id, summary={}
    )
    session.add(run)
    session.flush()

    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status="queued",
    )
    session.add(job)
    session.commit()

    store = SQLJobStore(lambda: session)
    record = store.get_job(str(job.id))

    assert record.params["retention_days"] == 1
    assert record.params["min_small_files"] == 10


def test_get_job_with_no_scanned_partitions_leaves_params_untouched(session):
    table, job = _seed_table_with_job(session)
    session.commit()

    store = SQLJobStore(lambda: session)
    record = store.get_job(str(job.id))

    assert "benefit_score" not in record.params
    assert "moop_score" not in record.params


def test_get_table_data_stats_include_delete_policy_fields(session):
    """cryo/planner/planner.py's Okolnychyi minor/major delete policy (§C2) reads
    position_delete_files and format_version off the stats dict build_plan() receives --
    previously missing from _stats_dict entirely, so the policy always saw 0/None regardless
    of what the scanner actually recorded."""
    from cryo.db.models import TableStats

    table, _job = _seed_table_with_job(session)
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=200,
            small_files_count=10,
            total_size_gb=20.0,
            total_delete_files=90,
            position_delete_files=80,
            equality_delete_files=10,
            delete_file_pct=3.5,
            raw={"format_version": 2},
        )
    )
    session.commit()

    store = SQLJobStore(lambda: session)
    data = store.get_table_data(str(table.id))

    assert data.stats["position_delete_files"] == 80
    assert data.stats["equality_delete_files"] == 10
    assert data.stats["format_version"] == 2
