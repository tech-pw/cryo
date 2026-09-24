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
from cryo.maintenance.coordinator import BatchMaintenance, RunReport
from cryo.maintenance.k8s_lanes import LaneSpec, LaneState, PollUpdate, lane_names
from cryo.maintenance.manifest import TableData
from cryo.maintenance.results import StatementResult
from cryo.queue.locks import InMemoryLockStore

NOW = datetime(2026, 9, 24, 19, 0, tzinfo=UTC)


@dataclass
class _Behaviour:
    fail_seq: dict[uuid.UUID, int] = field(default_factory=dict)
    # Table on which its lane's pod dies right after printing the table-start line.
    die_on: uuid.UUID | None = None


class _FakeLaneRunner:
    def __init__(self, run_id: uuid.UUID, behaviour: _Behaviour) -> None:
        self.run_id = run_id
        self.behaviour = behaviour
        self.lanes: list[LaneState] = []
        self.manifests: dict[int, dict[str, Any]] = {}
        self.torn_down = False
        self._polled = False

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
        if self._polled:
            return update
        self._polled = True
        for lane in self.lanes:
            for table in self.manifests[lane.spec.index]["tables"]:
                table_id = uuid.UUID(table["table_id"])
                if table_id == self.behaviour.die_on:
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
                            metrics={"rewritten_data_files_count": 40, "added_data_files_count": 2}
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
    ids = _seed(session_factory, tables=4)
    # One lane (small tables), so the lane runs the tables in rank order: t0, t1, t2, t3.
    behaviour = _Behaviour(fail_seq={ids[0]: 1}, die_on=ids[2])
    h = _harness(session_factory, behaviour=behaviour)

    report = h.coordinator.run(trigger="batch-cron")

    jobs = _jobs(session_factory)
    assert len(h.runners[0].lanes) == 1
    assert jobs[ids[0]].status == "partial" and "Commit conflict" in (jobs[ids[0]].error or "")
    assert jobs[ids[1]].status == "succeeded"
    assert jobs[ids[2]].status == "failed"
    assert "lane pod ended mid-table: OOMKilled (exit 137)" in (jobs[ids[2]].error or "")
    assert jobs[ids[3]].status == "skipped"
    assert "not reached" in (jobs[ids[3]].error or "")
    assert (report.succeeded, report.partial, report.failed, report.skipped) == (1, 1, 1, 1)
    with session_factory() as session:
        run = session.get(MaintenanceRun, report.run_id)
    assert run is not None and run.status == "partial"
    assert sorted(h.rescanned[0]) == sorted([ids[0], ids[1]])
    for table_id in ids:
        assert h.locks.acquire_table_lock(str(table_id), "someone-else", 60)


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


def test_nothing_eligible_still_records_the_run_and_reports(session_factory) -> None:
    _seed(session_factory, tables=0)
    h = _harness(session_factory)

    report = h.coordinator.run(trigger="batch-cron")

    assert report.selected == 0 and report.run_id is not None
    assert h.runners == []
    assert len(h.notified) == 1
