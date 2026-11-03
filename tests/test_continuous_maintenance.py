"""Continuous (drift-triggered) maintenance: preset config, evaluation, dispatch (#77)."""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import fakeredis
import pytest

from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    Source,
    Table,
    TableDrift,
    TableStats,
)
from cryo.db.repositories.continuous_store import SQLContinuousStore
from cryo.domain.enums import EngineType, JobStatus, Mode
from cryo.events import service as events_service
from cryo.governor.admission import AdmissionConfig
from cryo.governor.governor import _DEFERRED, Governor, _DestinationWave
from cryo.planner.job_coverage import ActiveJob
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler.continuous import (
    AUDIT_COALESCED,
    AUDIT_DEFERRED,
    AUDIT_RESCAN,
    AUDIT_TRIGGER,
    ContinuousConfig,
    DriftView,
    History,
    Verdict,
    evaluate,
    is_continuous_trigger,
    parse_continuous,
    validate_continuous_preset,
)
from cryo.scheduler.continuous_dispatch import (
    CONSUMER_GROUP,
    MAINTENANCE_EVAL_STREAM,
    ContinuousDispatcher,
)
from cryo.scheduler.rules import PresetConfig, TableCandidate
from cryo.scheduler.service import InMemorySchedulerStore, SchedulerService
from cryo.triggers.evaluator import TriggerResult, TriggerType
from cryo.worker.worker import DestinationConfig, InMemoryJobStore, MaintenanceJobRecord
from tests.auth_helpers import ADMIN_HEADERS
from tests.test_presets_api_db import ApiDb

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
CONTINUOUS = {"enabled": True, "max_commits": 10, "debounce_seconds": 300}


# ---- preset config and validation ------------------------------------------------------


def test_continuous_is_off_unless_a_preset_opts_in() -> None:
    assert parse_continuous(None) == (None, [])
    config, errors = parse_continuous({"enabled": False})
    assert errors == []
    assert config is not None and not config.enabled
    assert validate_continuous_preset(None, mode=Mode.MANIFEST_ONLY, params={}) == []


def test_enabled_continuous_needs_a_threshold_and_a_sane_debounce() -> None:
    _, errors = parse_continuous({"enabled": True})
    assert any("at least one threshold" in error for error in errors)
    _, errors = parse_continuous(
        {"enabled": True, "max_commits": 5, "debounce_seconds": 7200, "max_delay_seconds": 600}
    )
    assert "continuous.debounce_seconds: must not exceed max_delay_seconds" in errors
    _, errors = parse_continuous({"enabled": True, "max_commits": 0, "bogus": 1})
    assert "continuous.bogus: unknown field" in errors
    assert any(error.startswith("continuous.max_commits") for error in errors)


def test_continuous_presets_must_expire_the_snapshots_they_create() -> None:
    errors = validate_continuous_preset(CONTINUOUS, mode=Mode.MANIFEST_ONLY, params={})
    assert any("does not plan expire_snapshots" in error for error in errors)
    errors = validate_continuous_preset(
        CONTINUOUS, mode=Mode.STANDARD, params={"skip_operations": ["expire_snapshots"]}
    )
    assert any("skip_operations drops expire_snapshots" in error for error in errors)
    assert validate_continuous_preset(CONTINUOUS, mode=Mode.STANDARD, params={}) == []


def _destination(api_db: ApiDb) -> str:
    response = api_db.client.post(
        "/api/v1/destinations",
        headers=ADMIN_HEADERS,
        json={"name": "trino-main", "engine": "trino", "config": {"host": "trino"}},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def test_preset_api_round_trips_and_validates_the_continuous_block(api_db: ApiDb) -> None:
    client = api_db.client
    destination_id = _destination(api_db)
    base = {"destination_id": destination_id, "operations": ["optimize"]}

    created = client.post(
        "/api/v1/presets",
        headers=ADMIN_HEADERS,
        json={**base, "name": "streaming", "continuous": CONTINUOUS},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["continuous"]["enabled"] is True
    assert body["continuous"]["max_commits"] == 10
    assert body["continuous"]["min_interval_seconds"] == 1800

    plain = client.post("/api/v1/presets", headers=ADMIN_HEADERS, json={**base, "name": "plain"})
    assert plain.status_code == 201
    assert plain.json()["continuous"] is None

    no_threshold = client.post(
        "/api/v1/presets",
        headers=ADMIN_HEADERS,
        json={**base, "name": "bad", "continuous": {"enabled": True}},
    )
    assert no_threshold.status_code == 400
    assert "continuous" in no_threshold.json()["detail"]

    # Changing only the mode is checked against the stored continuous block.
    to_manifest_only = client.patch(
        f"/api/v1/presets/{body['id']}", headers=ADMIN_HEADERS, json={"mode": "manifest_only"}
    )
    assert to_manifest_only.status_code == 400
    assert "continuous" in to_manifest_only.json()["detail"]

    turned_off = client.patch(
        f"/api/v1/presets/{body['id']}", headers=ADMIN_HEADERS, json={"continuous": None}
    )
    assert turned_off.status_code == 200
    assert turned_off.json()["continuous"] is None


# ---- evaluation ------------------------------------------------------------------------


def _drift(commits: int = 12, *, first_ago: int = 3600, last_ago: int = 600) -> DriftView:
    return DriftView(
        commits=commits,
        first_commit_at=NOW - timedelta(seconds=first_ago),
        last_commit_at=NOW - timedelta(seconds=last_ago),
    )


def _config(**overrides: Any) -> ContinuousConfig:
    return replace(ContinuousConfig(enabled=True, max_commits=10), **overrides)


def test_below_every_threshold_is_idle() -> None:
    decision = evaluate(_config(), _drift(commits=3), History(), now=NOW)
    assert decision.verdict is Verdict.IDLE


def test_a_crossed_threshold_waits_for_writer_quiet() -> None:
    decision = evaluate(_config(), _drift(last_ago=30, first_ago=200), History(), now=NOW)
    assert decision.verdict is Verdict.WAIT
    assert decision.reason == "debounce"
    assert decision.retry_at == NOW - timedelta(seconds=30) + timedelta(seconds=300)


def test_max_delay_ends_the_debounce_for_a_writer_that_never_pauses() -> None:
    decision = evaluate(
        _config(max_delay_seconds=3600), _drift(last_ago=5, first_ago=4000), History(), now=NOW
    )
    assert decision.verdict is Verdict.DUE
    assert (decision.signal, decision.value, decision.threshold) == ("commits", 12.0, 10.0)


def test_min_interval_defers_after_recent_maintenance() -> None:
    history = History(last_maintained_at=NOW - timedelta(minutes=10))
    decision = evaluate(_config(min_interval_seconds=1800), _drift(), history, now=NOW)
    assert decision.verdict is Verdict.DEFER
    assert decision.reason == "min_interval"
    assert decision.retry_at == NOW + timedelta(minutes=20)


def test_rate_limits_and_churn_cap_defer() -> None:
    hour = History(runs_last_hour=2, oldest_run_last_hour=NOW - timedelta(minutes=50))
    decision = evaluate(_config(max_runs_per_hour=2), _drift(), hour, now=NOW)
    assert (decision.verdict, decision.reason) == (Verdict.DEFER, "rate_limit_hour")
    assert decision.retry_at == NOW + timedelta(minutes=10)

    day = History(runs_last_day=3, oldest_run_last_day=NOW - timedelta(hours=20))
    decision = evaluate(_config(max_runs_per_hour=None, max_runs_per_day=3), _drift(), day, now=NOW)
    assert (decision.verdict, decision.reason) == (Verdict.DEFER, "rate_limit_day")

    churn = History(maintenance_commits_last_day=48)
    decision = evaluate(_config(max_maintenance_commits_per_day=48), _drift(), churn, now=NOW)
    assert (decision.verdict, decision.reason) == (Verdict.DEFER, "churn_cap")


def test_other_signals_fire_independently() -> None:
    config = ContinuousConfig(enabled=True, max_delete_file_ratio=0.2, max_score_drop=10)
    drift = replace(_drift(commits=1), delete_file_ratio=0.25)
    assert evaluate(config, drift, History(), now=NOW).signal == "delete_ratio"
    drift = replace(_drift(commits=1), score_drop=15)
    assert evaluate(config, drift, History(), now=NOW).signal == "score_drop"


# ---- governor: continuous jobs take every gate ----------------------------------------


class _AlwaysSignal:
    def evaluate(self, session: Any, table_id: uuid.UUID, *, is_due_for_schedule: bool) -> Any:
        return TriggerResult(
            table_id=table_id,
            should_run=True,
            trigger_type=TriggerType.FILE_DEGRADATION,
            reason="small-file ratio crossed",
        )


def _gate_job(run_trigger: str) -> MaintenanceJobRecord:
    return MaintenanceJobRecord(
        id=f"job-{run_trigger}",
        run_id="run-1",
        table_id=str(uuid.uuid4()),
        destination_id="dest",
        mode=Mode.STANDARD,
        status=JobStatus.QUEUED,
        cooldown_hours=24,
        last_maintained_at=NOW - timedelta(hours=1),
        run_trigger=run_trigger,
    )


def test_continuous_jobs_do_not_take_the_signal_bypass_in_gate_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cryo.governor.governor._record_deferred_admission", lambda *_a, **_k: None)
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(id="dest", engine=EngineType.TRINO, config={}))
    cron_job = _gate_job("cron")
    continuous_job = _gate_job("continuous:commits")
    store.add_job(cron_job)
    store.add_job(continuous_job)
    governor = Governor(
        InMemoryMaintenanceQueue(),
        InMemoryLockStore(),
        store,
        default_config=AdmissionConfig(),
        session_factory=lambda: nullcontext(MagicMock()),
        trigger_evaluator=_AlwaysSignal(),  # type: ignore[arg-type]
        enable_signal_triggers=True,
    )
    wave = _DestinationWave(
        destination=DestinationConfig(id="dest", engine=EngineType.TRINO, config={}),
        destination_id="dest",
        config=AdmissionConfig(),
        live_capacity={},
        session=MagicMock(),
        current=NOW,
        approved_keys={},
        stats_cache={},
        paused_table_ids=set(),
        paused_effect_destination_ids=set(),
        fleet_decisions={},
    )

    # The table's signal fired, so a cron job skips cooldown...
    assert governor._gate_admission(cron_job, wave) is None
    # ...but a continuous job was created *because* a signal fired; it still waits out cooldown.
    assert governor._gate_admission(continuous_job, wave) == _DEFERRED
    assert store.get_job(continuous_job.id).status is JobStatus.DEFERRED
    assert is_continuous_trigger("continuous:commits")
    assert not is_continuous_trigger("cron")


def test_job_store_records_carry_the_run_trigger(session_factory: Any) -> None:
    from cryo.db.repositories.job_store import SQLJobStore

    with session_factory() as session:
        table_id, preset_id, destination_id = _seed_table(session)
        run = MaintenanceRun(
            id=uuid.uuid4(), kind="scheduled", preset_id=preset_id, trigger="continuous:commits"
        )
        session.add(run)
        job_id = uuid.uuid4()
        session.add(
            MaintenanceJob(
                id=job_id,
                run_id=run.id,
                table_id=table_id,
                destination_id=destination_id,
                mode="standard",
                status="queued",
            )
        )
        session.commit()
    record = SQLJobStore(session_factory).get_job(str(job_id))
    assert record is not None
    assert record.run_trigger == "continuous:commits"


# ---- dispatcher ------------------------------------------------------------------------


@dataclass
class _State:
    drift: DriftView
    history: History = field(default_factory=History)
    scan_at: datetime | None = NOW


class _FakeStore:
    def __init__(self, table: TableCandidate, state: _State) -> None:
        self.tables = {table.id: table}
        self.states = {table.id: state}
        self.audits: list[tuple[str, str, str, str]] = []
        self.rescans: list[str] = []
        self.drifted: list[str] = []

    def drift_table_ids(self, *, limit: int) -> list[str]:
        return self.drifted[:limit]

    def list_tables_by_ids(self, table_ids: Any) -> list[TableCandidate]:
        return [self.tables[tid] for tid in table_ids if tid in self.tables]

    def load_state(self, table_ids: Any, *, now: datetime, last_maintained: Any) -> Any:
        return {tid: self.states[tid] for tid in table_ids if tid in self.states}

    def audit(self, table_id: str, action: str, *, status: str, detail: str, engine: Any = None):
        self.audits.append((table_id, action, status, detail))

    def request_rescan(self, table_id: str, *, redis_client: Any) -> bool:
        self.rescans.append(table_id)
        return True


def _candidate(table_id: str = "t1") -> TableCandidate:
    return TableCandidate(id=table_id, schema_name="analytics", table_name="events")


def _preset(continuous: dict[str, Any] | None = CONTINUOUS) -> PresetConfig:
    return PresetConfig(
        id="p1",
        name="streaming",
        destination_id="dest",
        mode=Mode.STANDARD,
        is_default=True,
        schedule_cron="0 2 * * *",
        continuous=continuous,
    )


def _publish(redis: Any, table_id: str, snapshot_id: int, writer_commits: int = 1) -> None:
    redis.xadd(
        MAINTENANCE_EVAL_STREAM,
        {
            "table_id": table_id,
            "source_id": "s1",
            "trigger": "commit_event",
            "snapshot_id": str(snapshot_id),
            "writer_commits": str(writer_commits),
            "reconcile_due": "0",
        },
    )


class _Harness:
    def __init__(
        self,
        state: _State,
        *,
        preset: PresetConfig | None = None,
        coverage: dict[str, list[ActiveJob]] | None = None,
        rescan_wait_seconds: int = 0,
    ) -> None:
        self.redis = fakeredis.FakeRedis()
        self.store = _FakeStore(_candidate(), state)
        self.preset = preset or _preset()
        self.coverage = coverage if coverage is not None else {}
        self.dispatcher = ContinuousDispatcher(
            self.store, self.redis, rescan_wait_seconds=rescan_wait_seconds
        )
        self.submitted: list[Any] = []

    def tick(self, now: datetime = NOW) -> Any:
        from cryo.scheduler.continuous_dispatch import ContinuousContext
        from cryo.scheduler.scheduler import build_scheduled_run

        def build_run(preset: Any, table: Any, trigger: str, context: Any) -> Any:
            return build_scheduled_run(
                preset, [table], context.presets, context.rules, (), trigger=trigger, now=now
            )

        def submit(run: Any, preset: Any, tables: Any, context: Any) -> int:
            self.submitted.append(run)
            return len(run.jobs)

        return self.dispatcher.tick(
            now,
            load_context=lambda: ContinuousContext(
                presets=[self.preset], rules=[], assignments=[], coverage=self.coverage
            ),
            build_run=build_run,
            submit=submit,
        )

    def actions(self) -> list[str]:
        return [action for _, action, _, _ in self.store.audits]


def test_a_due_table_gets_one_run_through_the_normal_scheduler_path() -> None:
    harness = _Harness(_State(drift=_drift()))
    for snapshot_id in (101, 102, 103):
        _publish(harness.redis, "t1", snapshot_id)

    result = harness.tick()

    assert result.consumed == 3
    assert result.triggered == 1
    (run,) = harness.submitted
    assert run.trigger == "continuous:commits"
    assert [job.table_id for job in run.jobs] == ["t1"]
    assert harness.actions() == [AUDIT_TRIGGER]
    assert "merged_events=3" in harness.store.audits[0][3]
    assert harness.redis.zcard("cryo:continuous:pending") == 0


def test_replayed_stream_entries_are_idempotent_per_table_and_snapshot() -> None:
    harness = _Harness(_State(drift=_drift(last_ago=10, first_ago=20)))
    _publish(harness.redis, "t1", 101)
    _publish(harness.redis, "t1", 101)
    _publish(harness.redis, "t1", 102)
    harness.tick()
    assert harness.redis.hget("cryo:continuous:merged", "t1") == b"2"
    # A writer still committing: one pending entry for the table, no run yet.
    assert harness.redis.zcard("cryo:continuous:pending") == 1
    assert harness.submitted == []


def test_maintenance_only_entries_are_not_drift() -> None:
    harness = _Harness(_State(drift=_drift()))
    _publish(harness.redis, "t1", 101, writer_commits=0)
    harness.tick()
    assert harness.redis.zcard("cryo:continuous:pending") == 0
    assert harness.submitted == []


def test_an_active_job_coalesces_the_trigger_and_audits_once() -> None:
    coverage = {"t1": [ActiveJob(job_id="job-9", operations=frozenset({"optimize"}))]}
    harness = _Harness(_State(drift=_drift()), coverage=coverage)
    _publish(harness.redis, "t1", 101)
    harness.tick()
    harness.tick(NOW + timedelta(minutes=10))
    assert harness.submitted == []
    assert harness.actions() == [AUDIT_COALESCED]
    assert "active_job=job-9" in harness.store.audits[0][3]


def test_rate_limited_tables_are_deferred_with_one_audit_per_reason() -> None:
    history = History(runs_last_hour=2, oldest_run_last_hour=NOW - timedelta(minutes=30))
    harness = _Harness(_State(drift=_drift(), history=history))
    _publish(harness.redis, "t1", 101)
    harness.tick()
    harness.redis.zadd("cryo:continuous:pending", {"t1": NOW.timestamp()})
    harness.tick()
    assert harness.submitted == []
    assert harness.actions() == [AUDIT_DEFERRED]
    assert "rate_limit_hour" in harness.store.audits[0][3]
    score = harness.redis.zscore("cryo:continuous:pending", "t1")
    assert score == (NOW + timedelta(minutes=30)).timestamp()


def test_a_burst_of_100_commits_in_5_minutes_produces_one_job() -> None:
    start = NOW - timedelta(hours=1)
    state = _State(drift=DriftView(), scan_at=start)
    harness = _Harness(state)
    for commit in range(100):
        at = start + timedelta(seconds=3 * commit)
        state.drift = DriftView(
            commits=commit + 1,
            first_commit_at=start,
            last_commit_at=at,
        )
        _publish(harness.redis, "t1", 1000 + commit)
        harness.tick(at)
        if harness.submitted and not harness.coverage:
            harness.coverage["t1"] = [ActiveJob(job_id="job-1", operations=frozenset({"optimize"}))]
            state.history = History(last_continuous_at=at, runs_last_hour=1, runs_last_day=1)
    burst_end = start + timedelta(minutes=5)
    for minute in range(1, 31):
        at = burst_end + timedelta(minutes=minute)
        harness.tick(at)
        if harness.submitted and not harness.coverage:
            harness.coverage["t1"] = [ActiveJob(job_id="job-1", operations=frozenset({"optimize"}))]
            state.history = History(last_continuous_at=at, runs_last_hour=1, runs_last_day=1)

    assert len(harness.submitted) == 1
    assert harness.submitted[0].trigger == "continuous:commits"
    assert harness.actions().count(AUDIT_TRIGGER) == 1


def test_a_stale_scan_requests_a_rescan_before_planning() -> None:
    state = _State(drift=_drift(), scan_at=NOW - timedelta(days=1))
    harness = _Harness(state, rescan_wait_seconds=1800)
    _publish(harness.redis, "t1", 101)

    harness.tick()
    assert harness.store.rescans == ["t1"]
    assert harness.actions() == [AUDIT_RESCAN]
    assert harness.submitted == []

    # The rescan landed: the scan is newer than the drift, so the table is planned.
    harness.store.states["t1"] = replace(state, scan_at=NOW + timedelta(minutes=1))
    harness.tick(NOW + timedelta(minutes=3))
    assert len(harness.submitted) == 1
    assert harness.store.rescans == ["t1"]


def test_a_rescan_that_never_lands_does_not_block_maintenance_forever() -> None:
    state = _State(drift=_drift(), scan_at=NOW - timedelta(days=1))
    harness = _Harness(state, rescan_wait_seconds=1800)
    _publish(harness.redis, "t1", 101)
    harness.tick()
    harness.redis.zadd("cryo:continuous:pending", {"t1": 0})
    harness.tick(NOW + timedelta(minutes=31))
    assert len(harness.submitted) == 1
    assert "rescan did not finish in time" in harness.store.audits[-1][3]


def test_a_preset_without_continuous_drops_the_table() -> None:
    harness = _Harness(_State(drift=_drift()), preset=_preset(continuous=None))
    _publish(harness.redis, "t1", 101)
    harness.tick()
    assert harness.submitted == []
    assert harness.redis.zcard("cryo:continuous:pending") == 0


def test_the_drift_sweep_recovers_from_a_redis_flush() -> None:
    harness = _Harness(_State(drift=_drift()))
    harness.store.drifted = ["t1"]
    harness.redis.flushall()
    result = harness.tick()
    assert result.triggered == 1


def test_the_consumer_group_is_recreated_after_redis_loses_it() -> None:
    harness = _Harness(_State(drift=_drift(last_ago=10, first_ago=20)))
    _publish(harness.redis, "t1", 101)
    harness.tick()
    harness.redis.flushall()
    _publish(harness.redis, "t1", 102)
    harness.tick()
    groups = harness.redis.xinfo_groups(MAINTENANCE_EVAL_STREAM)
    assert [group["name"] for group in groups] == [CONSUMER_GROUP.encode()]
    assert harness.redis.zcard("cryo:continuous:pending") == 1


def test_events_and_scheduler_agree_on_the_stream_name() -> None:
    assert events_service.MAINTENANCE_EVAL_STREAM == MAINTENANCE_EVAL_STREAM


def test_scheduler_tick_persists_and_enqueues_continuous_runs() -> None:
    store = InMemorySchedulerStore()
    table = _candidate()
    store.tables = [table]
    store.presets = [replace(_preset(), schedule_cron=None)]
    queue = InMemoryMaintenanceQueue()
    redis = fakeredis.FakeRedis()
    fake = _FakeStore(table, _State(drift=_drift()))
    dispatcher = ContinuousDispatcher(fake, redis, rescan_wait_seconds=0)
    service = SchedulerService(store, queue, continuous=dispatcher)
    _publish(redis, "t1", 101)

    result = service.tick(now=NOW)

    assert result.runs_created == 1
    (run,) = store.runs
    assert run.trigger == "continuous:commits"
    assert queue.is_pending(run.jobs[0].id, "dest")


# ---- SQL store -------------------------------------------------------------------------


def _seed_table(
    session: Any, *, is_default: bool = False, continuous: dict[str, Any] | None = CONTINUOUS
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    source_id = uuid.uuid4()
    session.add(Source(id=source_id, name=f"lake-{source_id}", type="rest", config={}))
    destination_id = uuid.uuid4()
    session.add(
        Destination(id=destination_id, name=f"d-{destination_id}", engine="trino", config={})
    )
    preset_id = uuid.uuid4()
    session.add(
        Preset(
            id=preset_id,
            name=f"p-{preset_id}",
            destination_id=destination_id,
            mode="standard",
            operations=[],
            params={},
            guardrails={},
            is_default=is_default,
            continuous=continuous,
        )
    )
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name="analytics",
            table_name=f"events_{table_id.hex[:6]}",
            first_seen_at=datetime(2026, 10, 1, tzinfo=UTC),
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=NOW - timedelta(hours=6),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=95,
            total_files=500,
            small_files_count=0,
            total_size_gb=100.0,
            avg_file_size_mb=200.0,
            total_partitions=10,
            total_snapshots=20,
            total_delete_files=50,
            delete_file_pct=10.0,
            raw={},
        )
    )
    return table_id, preset_id, destination_id


def test_sql_store_reads_drift_history_and_churn(session_factory: Any) -> None:
    with session_factory() as session:
        table_id, preset_id, destination_id = _seed_table(session)
        session.add(
            TableDrift(
                table_id=table_id,
                commits=14,
                small_files_added=40,
                baseline_score=95,
                estimated_score=80,
                first_commit_at=NOW - timedelta(hours=2),
                last_commit_at=NOW - timedelta(minutes=5),
            )
        )
        for minutes_ago, trigger in (
            (30, "continuous:commits"),
            (300, "continuous:commits"),
            (20, "cron"),
        ):
            run = MaintenanceRun(
                id=uuid.uuid4(),
                kind="scheduled",
                preset_id=preset_id,
                trigger=trigger,
                created_at=NOW - timedelta(minutes=minutes_ago),
            )
            session.add(run)
            session.add(
                MaintenanceJob(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    table_id=table_id,
                    destination_id=destination_id,
                    mode="standard",
                    status="succeeded",
                )
            )
        for action in ("optimize", "optimize", "expire_snapshots"):
            session.add(
                AuditLog(
                    table_id=table_id,
                    action=action,
                    status="OK",
                    ts=NOW - timedelta(hours=1),
                )
            )
        session.commit()

    store = SQLContinuousStore(session_factory)
    assert store.drift_table_ids(limit=10) == [str(table_id)]
    state = store.load_state([str(table_id)], now=NOW, last_maintained={})[str(table_id)]
    assert state.drift.commits == 14
    assert state.drift.score_drop == 15
    assert state.drift.delete_file_ratio == pytest.approx(0.10)
    assert state.history.runs_last_hour == 1
    assert state.history.runs_last_day == 2
    assert state.history.maintenance_commits_last_day == 2
    assert state.scan_at == NOW - timedelta(hours=6)

    store.audit(str(table_id), AUDIT_TRIGGER, status="OK", detail="threshold crossed")
    with session_factory() as session:
        row = session.query(AuditLog).filter(AuditLog.action == AUDIT_TRIGGER).one()
        assert row.origin == "continuous"


def test_watch_targets_include_tables_under_continuous_presets(session_factory: Any) -> None:
    with session_factory() as session:
        table_id, _, _ = _seed_table(session, is_default=True)
        session.commit()

    targets = events_service.load_watch_targets(session_factory)

    assert [target.table_id for target in targets] == [str(table_id)]


def test_watch_targets_skip_non_streaming_tables_without_continuous(session_factory: Any) -> None:
    with session_factory() as session:
        _seed_table(session, is_default=True, continuous=None)
        session.commit()

    assert events_service.load_watch_targets(session_factory) == []


def test_sql_store_requests_a_one_table_scan(session_factory: Any) -> None:
    with session_factory() as session:
        table_id, _, _ = _seed_table(session)
        session.commit()
    redis = fakeredis.FakeRedis()

    assert SQLContinuousStore(session_factory).request_rescan(str(table_id), redis_client=redis)

    with session_factory() as session:
        run = session.query(MaintenanceRun).filter(MaintenanceRun.kind == "scan").one()
        assert run.trigger == "continuous"
        assert run.summary["table_ids"] == [str(table_id)]
