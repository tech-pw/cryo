"""cryo/maintenance/coordinator.py end to end against SQLite, with the Spark lanes faked.

The fake lane runner reads the real gzip manifests the coordinator builds, so these tests
cover selection -> planning -> lane packing -> results -> jobs/audit/run rows -> locks ->
rescan -> report, with only Kubernetes swapped out.
"""

from __future__ import annotations

import gzip
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from cryo.config import Settings
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableStats,
)
from cryo.maintenance.coordinator import BatchMaintenance, RunReport, TableOutcome, _audit_row
from cryo.maintenance.k8s_lanes import LaneSpec, LaneState, PollUpdate, lane_names
from cryo.maintenance.manifest import TableData
from cryo.maintenance.results import StatementResult
from cryo.queue.locks import InMemoryLockStore

NOW = datetime(2026, 9, 24, 19, 0, tzinfo=UTC)


@dataclass
class _Behaviour:
    fail_seq: dict[uuid.UUID, int] = field(default_factory=dict)
    # Tables on which their lane's pod dies right after printing the table-start line.
    die_on: set[uuid.UUID] = field(default_factory=set)
    # Lane pods that die before starting any table (bad image, catalog unreachable).
    die_at_start: bool = False
    # Lane indexes whose pod the node takes away before the first table (spot reclaim).
    node_lost_lanes: set[int] = field(default_factory=set)


class _FakeLaneRunner:
    def __init__(self, run_id: uuid.UUID, behaviour: _Behaviour) -> None:
        self.run_id = run_id
        self.behaviour = behaviour
        self.lanes: list[LaneState] = []
        self.manifests: dict[int, dict[str, Any]] = {}
        self.torn_down = False

    def launch(
        self, specs: Sequence[LaneSpec], *, deadline_epoch: float, active_deadline_seconds: int
    ) -> None:
        for spec in specs:
            self.manifests[spec.index] = json.loads(gzip.decompress(spec.manifest))
            name = lane_names(self.run_id, spec.index)
            self.lanes.append(LaneState(spec=spec, job_name=name, secret_name=name))

    @property
    def all_finished(self) -> bool:
        return all(lane.finished for lane in self.lanes)

    def poll(self) -> PollUpdate:
        update = PollUpdate()
        for lane in self.lanes:
            if lane.finished:
                continue
            if self.behaviour.die_at_start:
                lane.exit_reason = "Error (exit 1)"
            if lane.spec.index in self.behaviour.node_lost_lanes:
                lane.exit_reason = "lane pod deleted mid-run (spot reclaim); Job Failed"
                lane.infra_loss = True
            for table in self.manifests[lane.spec.index]["tables"]:
                if lane.exit_reason is not None:
                    break
                table_id = uuid.UUID(table["table_id"])
                if table_id in self.behaviour.die_on:
                    lane.started_tables.add(table_id)
                    lane.exit_reason = "OOMKilled (exit 137)"
                    break
                lane.started_tables.add(table_id)
                failed_at = self.behaviour.fail_seq.get(table_id)
                for stmt in table["statements"]:
                    seq = stmt["seq"]
                    if failed_at is not None and seq > failed_at:
                        result = StatementResult(
                            table_id, seq, stmt["action"], ok=False, skipped=True, error="skipped"
                        )
                    elif failed_at is not None and seq == failed_at:
                        result = StatementResult(
                            table_id, seq, stmt["action"], ok=False, error="Commit conflict"
                        )
                    else:
                        result = StatementResult(
                            table_id,
                            seq,
                            stmt["action"],
                            ok=True,
                            duration_ms=10,
                            rows=1,
                            metrics={
                                "rewritten_data_files_count": 40,
                                "added_data_files_count": 2,
                                "rewritten_bytes_count": 2**30,
                            }
                            if stmt["action"] == "optimize"
                            else {},
                        )
                    update.new_results.append((lane.spec.index, result))
            lane.finished = True
            update.newly_finished.append(lane)
        return update

    def teardown(self) -> None:
        self.torn_down = True


def _settings(**overrides: Any) -> Settings:
    return Settings(maintenance_poll_seconds=5, **overrides)


def _seed(session_factory: Any, *, tables: int = 3) -> list[uuid.UUID]:
    with session_factory() as session:
        source = Source(id=uuid.uuid4(), name="cdp", type="hive", config={}, enabled=True)
        session.add(source)
        session.add(
            Destination(
                id=uuid.uuid4(),
                name="spark-k8s-fallback",
                engine="spark_emr",
                enabled=True,
                config={"catalog": "cdp", "spark_k8s": {"namespace": "cryo"}},
            )
        )
        ids: list[uuid.UUID] = []
        for i in range(tables):
            table = Table(
                id=uuid.uuid4(),
                source_id=source.id,
                schema_name="demo",
                table_name=f"t{i}",
                is_active=True,
            )
            session.add(table)
            session.flush()
            session.add(
                TableStats(
                    table_id=table.id,
                    scan_at=NOW - timedelta(hours=6),
                    needs_compaction=True,
                    compaction_priority="HIGH",
                    grade="D",
                    overall_score=40,
                    total_files=1000,
                    small_files_count=900 - i,
                    total_size_gb=10.0 + i,
                    query_count_30d=1000 * (tables - i),
                )
            )
            ids.append(table.id)
        session.commit()
    return ids


def _load_table(session_factory: Any) -> Any:
    def load(table_id: uuid.UUID) -> TableData:
        with session_factory() as session:
            table = session.get(Table, table_id)
            assert table is not None
            return TableData(
                stats={
                    "catalog": "cdp",
                    "schema_name": table.schema_name,
                    "table_name": table.table_name,
                    "small_files_count": 500,
                    "total_files": 600,
                    "total_size_gb": 10,
                },
                partitions=[],
            )

    return load


@dataclass
class _Harness:
    coordinator: BatchMaintenance
    locks: InMemoryLockStore
    runners: list[_FakeLaneRunner]
    rescanned: list[list[uuid.UUID]]
    notified: list[RunReport]
    refreshed: list[bool]


def _harness(
    session_factory: Any,
    *,
    behaviour: _Behaviour | None = None,
    snapshot_age: timedelta | None = timedelta(hours=1),
    shared: frozenset[uuid.UUID] = frozenset(),
    refresh_raises: bool = False,
    settings: Settings | None = None,
) -> _Harness:
    locks = InMemoryLockStore()
    runners: list[_FakeLaneRunner] = []
    rescanned: list[list[uuid.UUID]] = []
    notified: list[RunReport] = []
    refreshed: list[bool] = []

    def factory(run_id: uuid.UUID, _cfg: Any, _catalog: str) -> _FakeLaneRunner:
        runner = _FakeLaneRunner(run_id, behaviour or _Behaviour())
        runners.append(runner)
        return runner

    def refresh(_session: Any) -> None:
        refreshed.append(True)
        if refresh_raises:
            raise RuntimeError("metastore unreachable")

    def snapshot(_session: Any) -> tuple[datetime | None, frozenset[uuid.UUID]]:
        return (None if snapshot_age is None else NOW - snapshot_age), shared

    def rescan(ids: Sequence[uuid.UUID]) -> int:
        rescanned.append(list(ids))
        return len(ids)

    def notify(report: RunReport) -> bool:
        notified.append(report)
        return True

    coordinator = BatchMaintenance(
        session_factory=session_factory,
        settings=settings or _settings(),
        lock_store=locks,
        lane_runner_factory=factory,
        load_table=_load_table(session_factory),
        protected_snapshot_ids=lambda _tid: [],
        refresh_shared_snapshot=refresh,
        shared_snapshot=snapshot,
        rescan=rescan,
        notify=notify,
        clock=lambda: NOW,
        sleep=lambda _s: None,
    )
    return _Harness(coordinator, locks, runners, rescanned, notified, refreshed)


def _jobs(session_factory: Any) -> dict[uuid.UUID, MaintenanceJob]:
    with session_factory() as session:
        return {job.table_id: job for job in session.scalars(select(MaintenanceJob))}


def test_happy_path_records_every_statement_rescans_and_reports_once(session_factory) -> None:
    ids = _seed(session_factory)
    h = _harness(session_factory)

    report = h.coordinator.run(trigger="batch-cron")

    jobs = _jobs(session_factory)
    assert {job.status for job in jobs.values()} == {"succeeded"}
    assert set(jobs) == set(ids)
    assert all(job.engine_ref and job.engine_ref.startswith("cryo-maint-") for job in jobs.values())
    with session_factory() as session:
        audit = list(session.scalars(select(AuditLog)))
        run = session.get(MaintenanceRun, report.run_id)
    manifests = h.runners[0].manifests.values()
    planned = sum(len(t["statements"]) for m in manifests for t in m["tables"])
    assert len(audit) == planned
    assert {a.status for a in audit} == {"OK"}
    assert {a.engine for a in audit} == {"spark_emr"}
    assert {a.actor for a in audit} == {"cryo-batch"}
    assert run is not None and run.status == "succeeded" and run.kind == "scheduled"
    assert run.summary["pipeline"] == "batch" and run.summary["succeeded"] == 3
    assert report.files_rewritten == 120 and report.files_added == 6
    assert h.runners[0].torn_down
    assert sorted(h.rescanned[0]) == sorted(ids)
    assert len(h.notified) == 1
    assert h.refreshed == [True]
    for table_id in ids:
        assert h.locks.acquire_table_lock(str(table_id), "someone-else", 60)


def test_one_table_failing_does_not_stop_the_rest_and_lane_death_is_attributed(
    session_factory,
) -> None:
    ids = _seed(session_factory, tables=5)
    # One lane (small tables), so the lane runs the tables in rank order: t0 .. t4.
    behaviour = _Behaviour(fail_seq={ids[0]: 1}, die_on={ids[2]})
    h = _harness(session_factory, behaviour=behaviour)

    report = h.coordinator.run(trigger="batch-cron")

    jobs = _jobs(session_factory)
    lanes = h.runners[0].lanes
    assert [lane.spec.index for lane in lanes] == [0, 1]
    assert lanes[1].spec.mem_mib == lanes[0].spec.mem_mib
    replacement = [uuid.UUID(t["table_id"]) for t in h.runners[0].manifests[1]["tables"]]
    assert replacement == [ids[3], ids[4]]
    assert jobs[ids[0]].status == "partial" and "Commit conflict" in (jobs[ids[0]].error or "")
    assert jobs[ids[1]].status == "succeeded"
    assert jobs[ids[2]].status == "failed"
    assert "lane pod ended mid-table: OOMKilled (exit 137)" in (jobs[ids[2]].error or "")
    assert jobs[ids[3]].status == "succeeded" and jobs[ids[4]].status == "succeeded"
    assert jobs[ids[3]].engine_ref == lane_names(report.run_id, 1)
    assert (report.succeeded, report.partial, report.failed, report.skipped) == (3, 1, 1, 0)
    assert report.lanes[-1]["replaces"] == 0
    with session_factory() as session:
        run = session.get(MaintenanceRun, report.run_id)
    assert run is not None and run.status == "partial"
    assert sorted(h.rescanned[0]) == sorted([ids[0], ids[1], ids[3], ids[4]])
    for table_id in ids:
        assert h.locks.acquire_table_lock(str(table_id), "someone-else", 60)


def test_replacement_lanes_stop_at_the_respawn_cap(session_factory) -> None:
    ids = _seed(session_factory, tables=4)
    behaviour = _Behaviour(die_on={ids[0], ids[1], ids[2]})
    h = _harness(
        session_factory,
        behaviour=behaviour,
        settings=_settings(maintenance_max_lane_respawns=1),
    )

    report = h.coordinator.run(trigger="batch-cron")

    jobs = _jobs(session_factory)
    assert len(h.runners[0].lanes) == 2
    assert jobs[ids[0]].status == "failed" and jobs[ids[1]].status == "failed"
    assert jobs[ids[2]].status == "skipped" and jobs[ids[3]].status == "skipped"
    assert "not reached" in (jobs[ids[3]].error or "")
    assert (report.failed, report.skipped) == (2, 2)


def test_a_lane_that_dies_before_its_first_table_is_not_replaced(session_factory) -> None:
    ids = _seed(session_factory, tables=3)
    h = _harness(session_factory, behaviour=_Behaviour(die_at_start=True))

    report = h.coordinator.run(trigger="batch-cron")

    assert len(h.runners[0].lanes) == 1
    jobs = _jobs(session_factory)
    assert {jobs[i].status for i in ids} == {"skipped"}
    assert "Error (exit 1)" in (jobs[ids[0]].error or "")
    assert report.skipped == 3


def test_a_lane_the_node_took_before_its_first_table_is_replaced(session_factory) -> None:
    """A spot reclaim or drain says nothing about the next pod, unlike a bad image."""
    ids = _seed(session_factory, tables=3)
    h = _harness(session_factory, behaviour=_Behaviour(node_lost_lanes={0}))

    report = h.coordinator.run(trigger="batch-cron")

    lanes = h.runners[0].lanes
    assert [lane.spec.index for lane in lanes] == [0, 1]
    replacement = {uuid.UUID(t["table_id"]) for t in h.runners[0].manifests[1]["tables"]}
    assert replacement == set(ids)
    jobs = _jobs(session_factory)
    assert {jobs[i].status for i in ids} == {"succeeded"}
    assert report.lanes[-1]["replaces"] == 0
    assert report.succeeded == 3


def test_lane_sql_rewrites_only_scanner_small_files_and_runs_no_ndv_scan(session_factory) -> None:
    _seed(session_factory, tables=2)
    h = _harness(session_factory, settings=_settings(scan_small_file_mb=64.0))

    h.coordinator.run(trigger="batch-cron")

    statements = [
        s for m in h.runners[0].manifests.values() for t in m["tables"] for s in t["statements"]
    ]
    optimize = [s["sql"] for s in statements if s["action"] == "optimize"]
    pinned = f"'min-file-size-bytes', '{64 * 1024 * 1024}'"
    assert optimize and all(pinned in sql for sql in optimize)
    assert not any("compute_table_stats" in s["sql"] for s in statements)


def test_a_table_locked_by_another_job_is_skipped_and_never_sent_to_spark(session_factory) -> None:
    ids = _seed(session_factory)
    h = _harness(session_factory)
    assert h.locks.acquire_table_lock(str(ids[1]), "legacy-worker-job", 3600)

    report = h.coordinator.run(trigger="batch-cron")

    jobs = _jobs(session_factory)
    assert jobs[ids[1]].status == "skipped"
    assert jobs[ids[1]].error == "table locked by another job"
    sent = {t["table_id"] for m in h.runners[0].manifests.values() for t in m["tables"]}
    assert str(ids[1]) not in sent
    assert report.locked == 1
    assert not h.locks.acquire_table_lock(str(ids[1]), "someone-else", 60)


def test_dry_run_plans_lanes_but_writes_nothing_and_starts_no_pods(session_factory) -> None:
    _seed(session_factory)
    h = _harness(session_factory)

    report = h.coordinator.run(trigger="batch-cron", dry_run=True)

    assert report.dry_run and report.run_id is None
    assert report.lanes and report.planned_statements > 0
    assert h.runners == [] and h.notified == [] and h.refreshed == []
    with session_factory() as session:
        assert session.scalars(select(MaintenanceRun)).first() is None


def test_stale_shared_snapshot_closes_the_orphan_gate_for_every_table(session_factory) -> None:
    _seed(session_factory)
    h = _harness(session_factory, snapshot_age=timedelta(days=5), refresh_raises=True)

    report = h.coordinator.run(trigger="batch-cron")

    actions = {
        s["action"]
        for m in h.runners[0].manifests.values()
        for t in m["tables"]
        for s in t["statements"]
    }
    assert report.orphan_gate_closed
    assert "remove_orphan_files" not in actions
    assert "optimize" in actions


def test_fresh_snapshot_keeps_orphan_removal_except_on_shared_tables(session_factory) -> None:
    ids = _seed(session_factory)
    h = _harness(session_factory, shared=frozenset({ids[0]}))

    h.coordinator.run(trigger="batch-cron")

    by_table = {
        uuid.UUID(t["table_id"]): {s["action"] for s in t["statements"]}
        for m in h.runners[0].manifests.values()
        for t in m["tables"]
    }
    assert "remove_orphan_files" not in by_table[ids[0]]
    assert "remove_orphan_files" in by_table[ids[1]]


def test_selection_skips_cooled_down_unreadable_and_stale_tables(session_factory) -> None:
    ids = _seed(session_factory, tables=4)
    with session_factory() as session:
        run = MaintenanceRun(id=uuid.uuid4(), kind="scheduled", status="succeeded")
        session.add(run)
        session.add(
            MaintenanceJob(
                id=uuid.uuid4(),
                run_id=run.id,
                table_id=ids[0],
                destination_id=session.scalars(select(Destination)).one().id,
                mode="deep",
                status="succeeded",
                finished_at=NOW - timedelta(hours=2),
            )
        )
        table = session.get(Table, ids[1])
        assert table is not None
        table.last_scan_error_kind = "metadata_missing"
        stats = session.get(TableStats, ids[2])
        assert stats is not None
        stats.scan_at = NOW - timedelta(days=10)
        session.commit()
    h = _harness(session_factory)

    report = h.coordinator.run(trigger="batch-cron", dry_run=True)

    assert report.selected == 1


def test_explicit_table_ids_bypass_cooldown_and_mark_the_run_adhoc(session_factory) -> None:
    ids = _seed(session_factory)
    h = _harness(session_factory)
    h.coordinator.run(trigger="batch-cron")

    report = h.coordinator.run(trigger="batch-adhoc", table_ids=[ids[0]])

    assert report.succeeded == 1
    with session_factory() as session:
        run = session.get(MaintenanceRun, report.run_id)
    assert run is not None and run.kind == "adhoc"


def test_sigterm_mid_run_tears_down_finalizes_jobs_and_still_reports(session_factory) -> None:
    ids = _seed(session_factory)
    h = _harness(session_factory)

    def poll_then_sigterm(self: _FakeLaneRunner) -> PollUpdate:
        raise SystemExit(143)

    original = _FakeLaneRunner.poll
    _FakeLaneRunner.poll = poll_then_sigterm  # type: ignore[method-assign]
    try:
        with pytest.raises(SystemExit):
            h.coordinator.run(trigger="batch-cron")
    finally:
        _FakeLaneRunner.poll = original  # type: ignore[method-assign]

    jobs = _jobs(session_factory)
    assert h.runners[0].torn_down
    assert {job.status for job in jobs.values()} == {"skipped"}
    assert all("run interrupted (SystemExit)" in (job.error or "") for job in jobs.values())
    with session_factory() as session:
        run = session.scalars(select(MaintenanceRun)).one()
    assert run.status == "failed" and run.finished_at is not None
    assert len(h.notified) == 1
    assert h.notified[0].failures[0]["error"] == "run interrupted (SystemExit)"
    for table_id in ids:
        assert h.locks.acquire_table_lock(str(table_id), "someone-else", 60)


def test_slack_message_leads_with_status_and_reports_before_after(session_factory) -> None:
    from cryo.maintenance.report import build_blocks

    _seed(session_factory)
    h = _harness(session_factory, behaviour=_Behaviour(fail_seq={}))
    report = h.coordinator.run(trigger="batch-cron")
    report.files_after, report.size_after_gb = 1500, report.size_before_gb - 1.0

    fallback, blocks = build_blocks(report, web_base_url="https://cryo.example")
    rendered = json.dumps([b.to_dict() for b in blocks], ensure_ascii=False)

    assert fallback.startswith("Cryo nightly maintenance: 3/3")
    assert "CLEAR" in rendered and "↓" in rendered
    assert f"https://cryo.example/runs/{report.run_id}" in rendered
    canary, _ = build_blocks(replace(report, trigger="batch-canary"))
    assert canary.startswith("Cryo maintenance (batch-canary)")


def test_autocomp_budget_cuts_the_selection_and_the_trace_lands_in_the_run_summary(
    session_factory,
) -> None:
    _seed(session_factory)
    h = _harness(session_factory, settings=_settings(maintenance_autocomp_budget_gbhr=12.0))

    report = h.coordinator.run(trigger="batch-cron")

    assert report.over_budget >= 1 and report.selected + report.over_budget == 3
    with session_factory() as session:
        run = session.get(MaintenanceRun, report.run_id)
    assert run is not None
    trace = run.summary["autocomp"]
    assert trace["budget_gbhr"] == 12.0 and trace["chosen_gbhr"] <= 12.0
    assert trace["weights"] == {"w_file_reduction": 0.7, "w_compute_cost": 0.3, "heat_weight": 0.3}
    assert trace["rewrite_rate_source"] == "setting" and trace["rewrite_gb_per_hour"] == 25.0
    assert run.summary["over_budget"] == report.over_budget


def test_rewrite_throughput_is_calibrated_from_recent_runs(session_factory) -> None:
    _seed(session_factory)
    with session_factory() as session:
        for hours_ago, rate in ((30, 40.0), (54, 60.0), (78, 1000.0)):
            session.add(
                MaintenanceRun(
                    id=uuid.uuid4(),
                    kind="scheduled",
                    trigger="batch-cron",
                    status="succeeded",
                    scheduled_at=NOW - timedelta(hours=hours_ago),
                    finished_at=NOW - timedelta(hours=hours_ago - 2),
                    summary={"pipeline": "batch", "observed_rewrite_gb_per_hour": rate},
                )
            )
        session.commit()
    h = _harness(session_factory)

    report = h.coordinator.run(trigger="batch-cron")

    assert report.autocomp is not None
    assert report.autocomp["rewrite_rate_source"] == "observed"
    assert report.autocomp["rewrite_gb_per_hour"] == 60.0


def test_rewrite_statements_are_recorded_as_duration_samples(session_factory) -> None:
    _seed(session_factory)
    h = _harness(session_factory)

    report = h.coordinator.run(trigger="batch-cron")

    with session_factory() as session:
        run = session.get(MaintenanceRun, report.run_id)
    assert run is not None
    samples = run.summary["duration_samples"]
    lane_cores = {lane["cpu_milli"] / 1000 for lane in report.lanes}
    assert samples and all(s["gib"] == 1.0 and s["seconds"] == 0.01 for s in samples)
    assert {s["cores"] for s in samples} <= lane_cores


def _seed_duration_history(session_factory: Any) -> None:
    # Ernest-shaped history: 120 s fixed cost plus 300 s per GiB per core. A pooled GiB/hour
    # rate cannot represent the fixed cost, so the model should win on the newest runs.
    with session_factory() as session:
        for run in range(12):
            samples = [
                {"gib": gib, "cores": cores, "seconds": 120 + 300 * gib / cores}
                for gib, cores in ((0.2, 1), (1.0, 1), (4.0, 2), (20.0, 4), (0.5, 2))
            ]
            session.add(
                MaintenanceRun(
                    id=uuid.uuid4(),
                    kind="scheduled",
                    trigger="batch-cron",
                    status="succeeded",
                    scheduled_at=NOW - timedelta(days=13 - run),
                    finished_at=NOW - timedelta(days=13 - run, hours=-2),
                    summary={"pipeline": "batch", "duration_samples": samples},
                )
            )
        session.commit()


def test_ernest_drives_cost_only_when_configured_and_better_on_held_out_runs(
    session_factory,
) -> None:
    _seed(session_factory)
    _seed_duration_history(session_factory)

    advisory = RunReport(run_id=None, trigger="batch-cron", dry_run=True, started_at=NOW)
    _harness(session_factory).coordinator._select(
        table_ids=None, limit=None, now=NOW, report=advisory
    )
    assert advisory.autocomp is not None
    trace = advisory.autocomp["duration_model"]
    assert trace["configured"] == "rate" and trace["used"] == "rate"
    assert trace["calibration"]["preferred"] is True
    assert trace["calibration"]["holdout_mape_ernest"] < 0.01

    report = RunReport(run_id=None, trigger="batch-cron", dry_run=True, started_at=NOW)
    h = _harness(session_factory, settings=_settings(maintenance_duration_model="ernest"))
    chosen = h.coordinator._select(table_ids=None, limit=None, now=NOW, report=report)
    assert report.autocomp is not None and report.autocomp["duration_model"]["used"] == "ernest"
    params = h.coordinator._autocomp_params
    assert params is not None and params.duration_hours is not None
    assert chosen and chosen[0].gbhr == pytest.approx(
        params.executor_memory_gb * params.duration_hours(chosen[0].candidate)
    )


def test_a_table_bigger_than_one_lane_is_capped_to_its_worst_chunks(session_factory) -> None:
    _seed(session_factory, tables=1)
    h = _harness(session_factory, settings=_settings(maintenance_max_runtime_minutes=30))
    report = RunReport(run_id=None, trigger="batch-cron", dry_run=True, started_at=NOW)

    (item,) = h.coordinator._select(table_ids=None, limit=None, now=NOW, report=report)

    assert item.capped and report.autocomp is not None and report.autocomp["capped"] == 1
    params = h.coordinator._autocomp_params
    assert params is not None and params.max_table_rewrite_gb is not None
    planned_params = h.coordinator._table_params({"chunk_size_gb": 1}, item)
    assert planned_params["max_chunks"] == max(1, int(params.max_table_rewrite_gb // 1))


def test_nothing_eligible_still_records_the_run_and_reports(session_factory) -> None:
    _seed(session_factory, tables=0)
    h = _harness(session_factory)

    report = h.coordinator.run(trigger="batch-cron")

    assert report.selected == 0 and report.run_id is not None
    assert h.runners == []
    assert len(h.notified) == 1


@pytest.mark.parametrize("measure", [False, True])
def test_every_lane_manifest_carries_the_orphan_measurement_setting(
    session_factory, measure: bool
) -> None:
    """Replacement lanes too: lane 0 is lost before its first table, so lane 1 replaces it."""
    _seed(session_factory)
    settings = _settings(maintenance_measure_orphan_bytes=True) if measure else _settings()
    h = _harness(session_factory, behaviour=_Behaviour(node_lost_lanes={0}), settings=settings)

    h.coordinator.run(trigger="batch-cron")

    manifests = h.runners[0].manifests
    assert sorted(manifests) == [0, 1]
    assert [manifests[i]["measure_orphan_bytes"] for i in (0, 1)] == [measure, measure]


def _outcome() -> TableOutcome:
    return TableOutcome(
        table_id=uuid.uuid4(),
        name="demo.t0",
        job_id=uuid.uuid4(),
        lane=0,
        statement_count=1,
        before_size_gb=10.0,
        before_files=1000,
    )


def test_audit_row_does_not_count_rewritten_bytes_as_freed() -> None:
    outcome = _outcome()
    metrics = {
        "rewritten_data_files_count": 40,
        "added_data_files_count": 2,
        "rewritten_bytes_count": 2**30,
    }
    result = StatementResult(outcome.table_id, 0, "optimize", ok=True, rows=1, metrics=metrics)

    row = _audit_row(outcome, result, "cryo-maint-lane-0")

    assert row.status == "OK" and row.actual_bytes_freed is None
    assert (row.input_files_count, row.output_files_count) == (40, 2)


_EXPIRED_NOTHING = {
    "deleted_data_files_count": 0,
    "deleted_position_delete_files_count": 0,
    "deleted_equality_delete_files_count": 0,
    "deleted_manifest_files_count": 0,
    "deleted_manifest_lists_count": 0,
}


@pytest.mark.parametrize(
    ("action", "metrics", "freed"),
    [
        ("rewrite_position_deletes", {"rewritten_bytes_count": 100, "added_bytes_count": 40}, 60),
        ("remove_orphan_files", {"bytes_removed": 7}, 7),
        ("remove_orphan_files", {}, None),
        ("expire_snapshots", _EXPIRED_NOTHING, 0),
        ("expire_snapshots", {**_EXPIRED_NOTHING, "deleted_data_files_count": 3}, None),
    ],
)
def test_audit_row_records_bytes_freed_only_as_measured(
    action: str, metrics: dict[str, Any], freed: int | None
) -> None:
    outcome = _outcome()
    result = StatementResult(outcome.table_id, 0, action, ok=True, rows=1, metrics=metrics)

    assert _audit_row(outcome, result, None).actual_bytes_freed == freed


def test_a_failed_statement_records_no_bytes_freed() -> None:
    outcome = _outcome()
    result = StatementResult(
        outcome.table_id,
        0,
        "remove_orphan_files",
        ok=False,
        error="boom",
        metrics={"bytes_removed": 7},
    )

    row = _audit_row(outcome, result, None)

    assert row.status == "FAILED" and row.actual_bytes_freed is None
