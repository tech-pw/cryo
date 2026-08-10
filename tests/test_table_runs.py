"""datalake.table_runs() -- the first endpoint that scopes runs/jobs by table_id (previously
none did; every frontend caller filtered client-side instead), and the first place before/after
TableStatsHistory deltas and resource-usage estimates get computed anywhere in the API layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import (
    Destination,
    JobStep,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableStatsHistory,
)
from cryo.db.repositories import datalake


def _seed(session, *, engine: str = "spark_emr", config: dict | None = None):
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(
        id=uuid4(),
        name=f"dest-{uuid4()}",
        engine=engine,
        config=config or {},
    )
    table = Table(id=uuid4(), source_id=source.id, schema_name="analytics", table_name="events")
    session.add_all([source, destination, table])
    session.flush()

    run = MaintenanceRun(
        id=uuid4(), kind="adhoc", trigger="claude-test", status="succeeded", summary={}
    )
    session.add(run)
    session.flush()

    started_at = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)
    finished_at = started_at + timedelta(hours=2)
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="deep",
        status="succeeded",
        started_at=started_at,
        finished_at=finished_at,
    )
    session.add(job)
    session.flush()

    session.add_all(
        [
            JobStep(
                id=uuid4(),
                job_id=job.id,
                step_index=0,
                operation_type="expire_snapshots",
                status="succeeded",
                started_at=started_at,
                finished_at=started_at + timedelta(minutes=5),
            ),
            JobStep(
                id=uuid4(),
                job_id=job.id,
                step_index=1,
                operation_type="optimize",
                status="succeeded",
                started_at=started_at + timedelta(minutes=5),
                finished_at=finished_at,
            ),
        ]
    )

    # Before snapshot: an hour before the job started. After: an hour after it finished.
    session.add_all(
        [
            TableStatsHistory(
                table_id=table.id,
                scan_at=started_at - timedelta(hours=1),
                grade="F",
                overall_score=24,
                total_files=100000,
                small_files_count=99000,
                total_size_gb=10.0,
                raw={"total_delete_files": 500, "delete_file_pct": 0.5},
            ),
            TableStatsHistory(
                table_id=table.id,
                scan_at=finished_at + timedelta(hours=1),
                grade="B",
                overall_score=82,
                total_files=4000,
                small_files_count=10,
                total_size_gb=9.8,
                raw={"total_delete_files": 0, "delete_file_pct": 0.0},
            ),
        ]
    )
    session.commit()
    return table, job


def test_table_runs_includes_steps_and_before_after_delta(session) -> None:
    table, job = _seed(
        session,
        config={
            "spark_k8s": {
                "executor_instances": 4,
                "executor_cpu_milli": 2000,
                "executor_mem_mib": 4096,
                "driver_cpu_milli": 1000,
                "driver_mem_mib": 2048,
            }
        },
    )

    result = datalake.table_runs(session, table.id, limit=50, offset=0)

    assert result is not None
    assert result["total"] == 1
    run_entry = result["items"][0]
    assert run_entry["job_id"] == job.id
    assert run_entry["status"] == "succeeded"
    assert run_entry["trigger"] == "claude-test"
    assert run_entry["duration_seconds"] == 2 * 3600

    steps = run_entry["steps"]
    assert [s["operation_type"] for s in steps] == ["expire_snapshots", "optimize"]

    assert run_entry["before_stats"]["grade"] == "F"
    assert run_entry["before_stats"]["total_delete_files"] == 500
    assert run_entry["after_stats"]["grade"] == "B"
    assert run_entry["after_stats"]["total_files"] == 4000

    effectiveness = run_entry["effectiveness"]
    assert effectiveness["file_count_reduction_pct"] == -96.0
    assert effectiveness["small_file_reduction_pct"] == -100.0
    assert effectiveness["byte_reduction_pct"] == -2.0
    assert effectiveness["headline"] == "96% fewer files, size 2% smaller"

    estimate = run_entry["resource_estimate"]
    assert estimate["engine"] == "spark_emr"
    # active_duration_seconds sums step durations (5min + 115min = 2h), not job duration --
    # both happen to match here since the steps are back-to-back with no gap.
    assert estimate["active_duration_seconds"] == 2 * 3600
    # vcpu_hours = (4 executors * 2.0 vCPU + 1.0 driver vCPU) * 2h = 18.0
    assert estimate["vcpu_hours"] == 18.0
    # memory_gib_hours = (4 executors * 4Gi + 1 driver * 2Gi) * 2h = 36.0
    assert estimate["memory_gib_hours"] == 36.0


def test_table_runs_resource_estimate_omits_compute_for_non_spark_engine(session) -> None:
    table, _job = _seed(session, engine="trino", config={})

    result = datalake.table_runs(session, table.id, limit=50, offset=0)

    estimate = result["items"][0]["resource_estimate"]
    assert estimate["engine"] == "trino"
    assert estimate["vcpu_hours"] is None
    assert estimate["memory_gib_hours"] is None
    # Duration is still meaningful without a compute estimate.
    assert estimate["active_duration_seconds"] == 2 * 3600


def test_table_runs_returns_none_for_missing_table(session) -> None:
    assert datalake.table_runs(session, uuid4(), limit=50, offset=0) is None


def test_table_runs_before_stats_none_when_no_prior_snapshot(session) -> None:
    """A table's very first run has nothing to diff against -- must not crash, just omit it."""
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={})
    table = Table(id=uuid4(), source_id=source.id, schema_name="analytics", table_name="events")
    session.add_all([source, destination, table])
    session.flush()

    run = MaintenanceRun(id=uuid4(), kind="adhoc", trigger="api", status="running", summary={})
    session.add(run)
    session.flush()

    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status="running",
        started_at=datetime(2026, 7, 30, tzinfo=UTC),
    )
    session.add(job)
    session.commit()

    result = datalake.table_runs(session, table.id, limit=50, offset=0)

    entry = result["items"][0]
    assert entry["before_stats"] is None
    assert entry["after_stats"] is None  # job hasn't finished yet
    assert entry["duration_seconds"] is None


def test_table_runs_queued_job_with_no_started_at_does_not_crash(session) -> None:
    """A queued job (blocked on a destination's slot_cap, e.g.) has started_at=None -- this hit
    a real production 500: _nearest_stats_snapshot(before_or_at=None, after_or_at=None) fell
    through to a `scan_at >= None` comparison, which SQLAlchemy rejects against Postgres."""
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="spark_k8s", config={})
    table = Table(id=uuid4(), source_id=source.id, schema_name="analytics", table_name="events")
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
        started_at=None,
        finished_at=None,
    )
    session.add(job)
    session.commit()

    result = datalake.table_runs(session, table.id, limit=50, offset=0)

    entry = result["items"][0]
    assert entry["status"] == "queued"
    assert entry["before_stats"] is None
    assert entry["after_stats"] is None
    assert entry["duration_seconds"] is None
    assert entry["effectiveness"] is None


def test_effectiveness_omitted_when_before_or_after_missing(session) -> None:
    """A table's first run (no before) or a job that hasn't finished (no after) has nothing
    to diff -- must not crash, just omit the field, same as before_stats/after_stats do."""
    from cryo.db.repositories.datalake import _run_effectiveness_payload

    assert _run_effectiveness_payload(None, {"total_files": 10}) is None
    assert _run_effectiveness_payload({"total_files": 10}, None) is None
    assert _run_effectiveness_payload(None, None) is None


def test_effectiveness_reports_large_file_count_win_even_when_bytes_barely_move() -> None:
    """Regression, 2026-08-10: mview.gold_dbt_drip_marketing_flow's real compaction cut file
    count 2,549 -> 268 (89%) while total size only moved -4% -- a genuinely large S3 request-
    cost win that a size-only summary would make look unremarkable. Both numbers must be
    independently visible, not blended into one."""
    from cryo.db.repositories.datalake import _run_effectiveness_payload

    result = _run_effectiveness_payload(
        {"total_files": 2549, "small_files_count": 2548, "total_size_gb": 64.0},
        {"total_files": 268, "small_files_count": 0, "total_size_gb": 61.1},
    )
    assert result["file_count_reduction_pct"] == -89.5
    assert result["small_file_reduction_pct"] == -100.0
    assert result["byte_reduction_pct"] == -4.5
    assert "fewer files" in result["headline"]
    assert "smaller" in result["headline"]


def test_effectiveness_handles_a_file_count_increase() -> None:
    """A table that regressed (more files after than before, e.g. a failed/partial run that
    still shows organic growth) should report a positive pct and say "more", not silently
    show a negative-looking reduction."""
    from cryo.db.repositories.datalake import _run_effectiveness_payload

    result = _run_effectiveness_payload(
        {"total_files": 100, "total_size_gb": 10.0},
        {"total_files": 150, "total_size_gb": 10.0},
    )
    assert result["file_count_reduction_pct"] == 50.0
    assert "more files" in result["headline"]
    assert "unchanged" in result["headline"]


def test_effectiveness_zero_baseline_reports_that_metric_as_none_not_a_crash() -> None:
    """Dividing by a zero baseline (e.g. total_size_gb tracked as 0 for a metadata-only
    table) must not raise -- that specific metric reports as unavailable rather than
    crashing the whole payload, as long as at least one other metric has a real baseline."""
    from cryo.db.repositories.datalake import _run_effectiveness_payload

    result = _run_effectiveness_payload(
        {"total_files": 100, "total_size_gb": 0.0},
        {"total_files": 80, "total_size_gb": 0.1},
    )
    assert result["byte_reduction_pct"] is None
    assert result["file_count_reduction_pct"] == -20.0


def test_effectiveness_all_zero_baselines_returns_none_entirely() -> None:
    from cryo.db.repositories.datalake import _run_effectiveness_payload

    result = _run_effectiveness_payload(
        {"total_files": 0, "total_size_gb": 0.0},
        {"total_files": 5, "total_size_gb": 0.1},
    )
    assert result is None
