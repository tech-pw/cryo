"""Repeat-maintenance gating: cron markers surviving a Redis restart (#177), signal triggers on
stale stats (#178), the expire_snapshots minimum interval (#179), and max_chunks truncation
(#199)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    Source,
    Table,
    TableStats,
)
from cryo.db.repositories.job_store import SQLJobStore
from cryo.db.repositories.scheduler_store import SQLSchedulerStore
from cryo.domain.enums import Mode, OperationType
from cryo.maintenance.selection import load_candidates
from cryo.planner.planner import build_plan
from cryo.planner.serialization import plan_from_dict, plan_to_dict
from cryo.scanner.scheduler import ScanSchedulerService
from cryo.scheduler.cron import cron_is_due
from cryo.triggers.evaluator import TriggerEvaluator, _stale_signal_reason
from cryo.triggers.signals import SignalResult

NOW = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)


class _Redis:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


def _source_table(session, **table_kwargs: Any) -> Table:
    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4()}", type="rest", config={})
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=f"t_{uuid.uuid4().hex[:8]}",
        is_active=True,
        **table_kwargs,
    )
    session.add_all([source, table])
    session.flush()
    return table


# --- #177 ---------------------------------------------------------------------------------


def _cron_preset(session) -> Preset:
    destination = Destination(
        id=uuid.uuid4(), name=f"dest-{uuid.uuid4()}", engine="trino", config={}
    )
    preset = Preset(
        id=uuid.uuid4(),
        name=f"preset-{uuid.uuid4()}",
        destination_id=destination.id,
        mode="standard",
        operations=["optimize"],
        params={},
        guardrails={},
        enabled=True,
        schedule_cron="0 2 * * *",
    )
    session.add_all([destination, preset])
    session.commit()
    return preset


def test_cron_preset_does_not_refire_after_redis_loses_its_marker(session) -> None:
    preset = _cron_preset(session)
    fired_at = NOW.replace(hour=2, minute=0)
    session.add(
        MaintenanceRun(
            id=uuid.uuid4(),
            kind="scheduled",
            preset_id=preset.id,
            trigger="cron",
            status="queued",
            created_at=fired_at,
        )
    )
    session.commit()
    store = SQLSchedulerStore(lambda: session, redis_client=_Redis())

    due = {config.id: last for config, last in store.list_due_presets(NOW)}

    assert due[str(preset.id)] == fired_at
    assert not cron_is_due("0 2 * * *", now=NOW, last_run_at=due[str(preset.id)])
    # Without the Postgres fallback this is exactly the re-fire the issue measured.
    assert cron_is_due("0 2 * * *", now=NOW, last_run_at=None)


def test_newer_redis_marker_still_wins_over_the_last_run_row(session) -> None:
    preset = _cron_preset(session)
    session.add(
        MaintenanceRun(
            id=uuid.uuid4(),
            kind="scheduled",
            preset_id=preset.id,
            trigger="cron",
            status="succeeded",
            created_at=NOW - timedelta(days=2),
        )
    )
    session.commit()
    marker = NOW - timedelta(hours=1)
    redis = _Redis({f"cryo:scheduler:last_run:{preset.id}": marker.isoformat()})
    store = SQLSchedulerStore(lambda: session, redis_client=redis)

    due = {config.id: last for config, last in store.list_due_presets(NOW)}

    assert due[str(preset.id)] == marker


def test_non_cron_runs_do_not_count_as_a_cron_fire(session) -> None:
    preset = _cron_preset(session)
    session.add(
        MaintenanceRun(
            id=uuid.uuid4(),
            kind="adhoc",
            preset_id=preset.id,
            trigger="api",
            status="queued",
            created_at=NOW - timedelta(hours=1),
        )
    )
    session.commit()
    store = SQLSchedulerStore(lambda: session, redis_client=_Redis())

    due = {config.id: last for config, last in store.list_due_presets(NOW)}

    assert due[str(preset.id)] is None


def test_scan_scheduler_falls_back_to_the_last_cron_scan_run(session) -> None:
    fired_at = NOW.replace(hour=2, minute=0)
    session.add(
        MaintenanceRun(
            id=uuid.uuid4(), kind="scan", trigger="cron", status="succeeded", created_at=fired_at
        )
    )
    session.add(
        MaintenanceRun(
            id=uuid.uuid4(),
            kind="scan",
            trigger="api",
            status="succeeded",
            created_at=NOW - timedelta(minutes=5),
        )
    )
    session.commit()
    service = ScanSchedulerService(queue=None, redis_client=_Redis())  # type: ignore[arg-type]

    assert service._last_run_at(session) == fired_at


# --- #178 ---------------------------------------------------------------------------------


def _audit(session, table: Table, *, ts: datetime, action: str = "optimize") -> None:
    session.add(AuditLog(ts=ts, table_id=table.id, action=action, status="OK"))


def _stats(session, table: Table, *, scan_at: datetime) -> None:
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=scan_at,
            needs_compaction=True,
            compaction_priority="high",
            grade="C",
            overall_score=50,
            total_files=100,
            small_files_count=80,
            total_size_gb=10.0,
            raw={},
        )
    )


def test_signal_on_stats_older_than_the_last_compaction_is_ignored(session) -> None:
    table = _source_table(session)
    _stats(session, table, scan_at=NOW - timedelta(hours=12))
    _audit(session, table, ts=NOW - timedelta(hours=5))
    session.commit()

    reason = _stale_signal_reason(session, table.id, min_interval=timedelta(hours=2), now=NOW)

    assert reason is not None and "predate the last maintenance" in reason


def test_signal_inside_the_minimum_interval_is_ignored(session) -> None:
    table = _source_table(session)
    _audit(session, table, ts=NOW - timedelta(minutes=30))
    _stats(session, table, scan_at=NOW - timedelta(minutes=10))
    session.commit()

    reason = _stale_signal_reason(session, table.id, min_interval=timedelta(hours=2), now=NOW)

    assert reason is not None and "minimum interval" in reason


def test_signal_backed_by_a_fresh_scan_may_override_the_cooldown(session) -> None:
    table = _source_table(session)
    _audit(session, table, ts=NOW - timedelta(hours=5))
    _stats(session, table, scan_at=NOW - timedelta(hours=1))
    never_maintained = _source_table(session)
    session.commit()

    assert _stale_signal_reason(session, table.id, min_interval=timedelta(hours=2), now=NOW) is None
    assert (
        _stale_signal_reason(session, never_maintained.id, min_interval=timedelta(hours=2), now=NOW)
        is None
    )


def test_expire_only_audits_do_not_suppress_signals(session) -> None:
    table = _source_table(session)
    _audit(session, table, ts=NOW - timedelta(minutes=5), action="expire_snapshots")
    _stats(session, table, scan_at=NOW - timedelta(days=1))
    session.commit()

    assert _stale_signal_reason(session, table.id, min_interval=timedelta(hours=2), now=NOW) is None


def test_evaluator_does_not_admit_on_a_stale_signal(session, monkeypatch) -> None:
    table = _source_table(session)
    _stats(session, table, scan_at=datetime.now(UTC) - timedelta(days=1))
    _audit(session, table, ts=datetime.now(UTC) - timedelta(hours=6))
    session.commit()
    monkeypatch.setattr(
        "cryo.triggers.evaluator.check_all_signals",
        lambda *_a, **_k: [
            SignalResult(triggered=True, signal_name="file_degradation", reason="80% small")
        ],
    )

    result = TriggerEvaluator().evaluate(session, table.id, is_due_for_schedule=False)

    assert result.should_run is False
    assert "predate" in result.reason


# --- #179 ---------------------------------------------------------------------------------


def _ops(plan) -> list[OperationType]:
    return [op.type for op in plan.operations]


def _expire_stats(**extra: Any) -> dict[str, Any]:
    return {"schema_name": "demo", "table_name": "events", "small_files_count": 0, **extra}


def test_expire_snapshots_is_skipped_inside_the_minimum_interval() -> None:
    plan = build_plan(_expire_stats(hours_since_last_expire_snapshots=3.0), [], Mode.LIGHT, {})

    assert OperationType.EXPIRE_SNAPSHOTS not in _ops(plan)
    assert any("Skipped expire_snapshots" in note for note in plan.notes)


def test_expire_snapshots_runs_once_the_interval_has_passed() -> None:
    plan = build_plan(_expire_stats(hours_since_last_expire_snapshots=30.0), [], Mode.LIGHT, {})

    assert OperationType.EXPIRE_SNAPSHOTS in _ops(plan)


def test_expire_snapshots_without_history_or_with_interval_zero_runs() -> None:
    assert OperationType.EXPIRE_SNAPSHOTS in _ops(build_plan(_expire_stats(), [], Mode.LIGHT, {}))
    assert OperationType.EXPIRE_SNAPSHOTS in _ops(
        build_plan(
            _expire_stats(hours_since_last_expire_snapshots=0.5),
            [],
            Mode.LIGHT,
            {"expire_snapshots_min_interval_hours": 0},
        )
    )


def test_default_interval_follows_a_tightened_retention_window() -> None:
    # A heavily backlogged table gets retention_days=1, so the default interval is 12h.
    backlogged = _expire_stats(total_snapshots=1000)
    assert OperationType.EXPIRE_SNAPSHOTS not in _ops(
        build_plan({**backlogged, "hours_since_last_expire_snapshots": 11.0}, [], Mode.LIGHT, {})
    )
    assert OperationType.EXPIRE_SNAPSHOTS in _ops(
        build_plan({**backlogged, "hours_since_last_expire_snapshots": 13.0}, [], Mode.LIGHT, {})
    )


def test_table_data_reports_hours_since_the_last_successful_expiry(
    session, session_factory
) -> None:
    table = _source_table(session)
    session.add(
        AuditLog(
            ts=datetime.now(UTC) - timedelta(hours=4),
            table_id=table.id,
            action="expire_snapshots",
            status="OK",
        )
    )
    session.add(
        AuditLog(
            ts=datetime.now(UTC) - timedelta(minutes=5),
            table_id=table.id,
            action="expire_snapshots",
            status="FAILED",
        )
    )
    session.commit()

    stats = SQLJobStore(session_factory).get_table_data(str(table.id)).stats

    assert 3.9 < stats["hours_since_last_expire_snapshots"] < 4.1


# --- #199 ---------------------------------------------------------------------------------


def _partitioned_stats() -> dict[str, Any]:
    return {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 5000,
        "total_size_gb": 5000,
        "partition_fields": [
            {
                "base_column": "event_date",
                "partition_field": "event_date_day",
                "transform": "day",
                "col_type": "date",
            }
        ],
    }


def _oversize_partitions(count: int) -> list[dict[str, Any]]:
    return [
        {
            "partition_value": f"2026-10-{day:02d}",
            "small_files_in_partition_count": 1000 - day,
            "partition_size_gb": 200,
        }
        for day in range(1, count + 1)
    ]


def test_max_chunks_truncation_is_recorded_on_the_plan() -> None:
    plan = build_plan(
        _partitioned_stats(),
        _oversize_partitions(10),
        Mode.STANDARD,
        {"min_small_files": 10, "chunk_size_gb": 50, "max_chunks": 2},
    )

    optimizes = [op for op in plan.operations if op.type is OperationType.OPTIMIZE]
    assert len(optimizes) == 2
    assert plan.optimize_truncation is not None
    assert plan.optimize_truncation.planned_partitions == 2
    assert plan.optimize_truncation.candidate_partitions == 10
    assert any("OPTIMIZE limited to 2 of 10" in note for note in plan.notes)
    # Best-first packing: the two partitions with the most small files are the ones kept.
    kept = {str(op.predicates) for op in optimizes}
    assert any("2026-10-01" in text for text in kept)
    assert any("2026-10-02" in text for text in kept)

    payload = plan_to_dict(plan)
    assert payload["optimize_truncation"] == {
        "planned_partitions": 2,
        "candidate_partitions": 10,
        "max_chunks": 2,
    }
    assert plan_from_dict(payload).optimize_truncation == plan.optimize_truncation


def test_untruncated_plan_carries_no_truncation_field() -> None:
    plan = build_plan(
        _partitioned_stats(),
        _oversize_partitions(2),
        Mode.STANDARD,
        {"min_small_files": 10, "chunk_size_gb": 50, "max_chunks": 5},
    )

    assert plan.optimize_truncation is None
    assert "optimize_truncation" not in plan_to_dict(plan)


def _finished_job(session, table: Table, *, plan: dict[str, Any]) -> None:
    run = MaintenanceRun(id=uuid.uuid4(), kind="batch", trigger="cron", status="succeeded")
    session.add(run)
    session.flush()
    session.add(
        MaintenanceJob(
            id=uuid.uuid4(),
            run_id=run.id,
            table_id=table.id,
            destination_id=uuid.uuid4(),
            mode="deep",
            status="succeeded",
            finished_at=NOW - timedelta(hours=2),
            plan=plan,
        )
    )


def test_truncated_run_does_not_start_the_nightly_cooldown(session) -> None:
    truncated = _source_table(session)
    full = _source_table(session)
    for table in (truncated, full):
        session.add(
            TableStats(
                table_id=table.id,
                scan_at=NOW - timedelta(hours=1),
                total_files=1000,
                small_files_count=900,
                total_size_gb=500,
                needs_compaction=True,
                compaction_priority="high",
                grade="C",
                overall_score=50,
                raw={},
            )
        )
    base_plan = {"table_ref": "x", "mode": "deep", "operations": [], "notes": []}
    _finished_job(
        session,
        truncated,
        plan={
            **base_plan,
            "optimize_truncation": {
                "planned_partitions": 2,
                "candidate_partitions": 10,
                "max_chunks": 2,
            },
        },
    )
    _finished_job(session, full, plan=base_plan)
    session.commit()

    ids = {
        c.table_id
        for c in load_candidates(session, now=NOW, cooldown_hours=24, max_stats_age_days=7)
    }

    assert truncated.id in ids
    assert full.id not in ids
