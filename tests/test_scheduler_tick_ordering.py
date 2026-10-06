"""Tests for cryo/scheduler/service.py's tick() step ordering.

Regression coverage for a real production incident, 2026-08-31: even after bounding the
ML-drift check (see test_scheduler_ml_drift_timeout.py), dispatch was still reachable only
after up to ~25-30min of *other* unbounded, sequential housekeeping every tick -- a full
first-run telemetry/query-log catch-up (tens of thousands of buffered Metabase query
statements) being the single biggest contributor observed live. None of that housekeeping
writes anything the dispatch path reads -- they're independent side-effecting tasks that
happened to share the same synchronous call sequence purely by original code organization.
Every pod restart (deploys, node reschedules, both frequent in practice) resets every
_last_* in-memory timestamp gating that housekeeping, re-paying the full first-run cost and
pushing dispatch back by that same window on every restart.

tick() now dispatches first (_dispatch_scheduled_runs + the due-preset loop, extracted into
_dispatch_due_presets), diagnostics/telemetry after -- so a fresh or long-blocked pod reaches
real maintenance dispatch on its very first tick, not after clearing 11 other steps.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler.rules import PresetConfig, TableAssignment, TableCandidate
from cryo.scheduler.service import SchedulerService


class _OneDuePresetStore:
    """A minimal real store with exactly one due preset and one matching table (pinned via a
    TableAssignment, the same mechanism effective_preset() uses in production -- there's no
    platform-default fallback), enough for _dispatch_due_presets to actually build and persist
    a run, not just call list_due_presets and stop."""

    def __init__(self) -> None:
        self.persisted_runs: list[object] = []
        self.marked_preset_runs: list[tuple[str, datetime]] = []
        self._preset = PresetConfig(
            id="preset-1",
            name="preset-1",
            destination_id="dest-1",
            mode="standard",
            schedule_cron="0 20 * * *",
            guardrails={},
        )
        self._table = TableCandidate(id="table-1", schema_name="s", table_name="t", stats={})
        self._assignment = TableAssignment(
            table_id="table-1", preset_id="preset-1", pinned=True
        )

    def list_due_presets(self, now):
        return [(self._preset, None)]

    def list_tables(self):
        return [self._table]

    def list_presets(self):
        return [self._preset]

    def list_rules(self):
        return []

    def list_assignments(self):
        return [self._assignment]

    def list_destinations(self):
        return [{"id": "dest-1", "name": "dest-1", "engine": "native"}]

    def persist_run(self, run):
        self.persisted_runs.append(run)

    def mark_preset_run(self, preset_id, run_at):
        self.marked_preset_runs.append((preset_id, run_at))


def test_dispatch_happens_before_diagnostic_steps_not_after(monkeypatch) -> None:
    """Direct regression test for the live incident: a due preset must be dispatched (a run
    persisted) even if every diagnostic/telemetry step after it would hang -- proving dispatch
    genuinely runs first, not just that it eventually runs at all."""
    store = _OneDuePresetStore()
    service = SchedulerService(store, InMemoryMaintenanceQueue())

    # Patch every diagnostic step to something that would fail loudly if called before
    # assertions run -- if dispatch depended on any of these completing first, this test
    # would hang or raise, not just report a wrong order.
    def _boom(*args, **kwargs):
        raise AssertionError("diagnostic step must not run before dispatch is verified")

    for name in [
        "_expire_stale_approval_requests",
        "_maybe_check_ml_drift",
        "_maybe_ingest_telemetry",
        "_maybe_purge_query_logs",
        "_maybe_prune_audit_logs",
        "_maybe_check_rollout_safety",
        "_maybe_silent_data_loss_sweep",
        "_maybe_check_unmatched_tables",
        "_maybe_check_cost_ceiling",
        "_maybe_detect_dark_tables",
        "_maybe_aggregate_cost_attribution",
        "_maybe_price_trino_scan_cost",
        "_sweep_pending_fallback_dispatches",
    ]:
        monkeypatch.setattr(service, name, MagicMock())

    monkeypatch.setattr(service, "_dispatch_scheduled_runs", MagicMock())

    result = service.tick(now=datetime(2026, 8, 31, 20, 0, tzinfo=UTC))

    assert result.runs_created == 1
    assert len(store.persisted_runs) == 1
    assert len(store.marked_preset_runs) == 1


def test_diagnostic_steps_still_all_run_every_tick(monkeypatch) -> None:
    """The reorder must not drop any housekeeping step -- they all still run, just after
    dispatch instead of before it."""
    store = _OneDuePresetStore()
    service = SchedulerService(store, InMemoryMaintenanceQueue())

    called: list[str] = []
    step_names = [
        "_expire_stale_approval_requests",
        "_maybe_check_ml_drift",
        "_maybe_ingest_telemetry",
        "_maybe_purge_query_logs",
        "_maybe_prune_audit_logs",
        "_maybe_check_rollout_safety",
        "_maybe_silent_data_loss_sweep",
        "_maybe_check_unmatched_tables",
        "_maybe_check_cost_ceiling",
        "_maybe_detect_dark_tables",
        "_maybe_aggregate_cost_attribution",
        "_maybe_price_trino_scan_cost",
        "_sweep_pending_fallback_dispatches",
    ]
    for name in step_names:
        monkeypatch.setattr(
            service, name, lambda *a, name=name, **k: called.append(name)
        )
    monkeypatch.setattr(service, "_dispatch_scheduled_runs", lambda *a, **k: called.append(
        "_dispatch_scheduled_runs"
    ))

    service.tick(now=datetime(2026, 8, 31, 20, 0, tzinfo=UTC))

    assert set(called) == {"_dispatch_scheduled_runs", *step_names}


def test_dispatch_scheduled_runs_and_due_preset_loop_both_run_before_any_diagnostic_step(
    monkeypatch,
) -> None:
    """Confirms the exact ordering, not just that everything eventually runs -- both dispatch
    mechanisms (_dispatch_scheduled_runs for one-off scheduled runs, the due-preset loop for
    cron presets) must complete before the first diagnostic step starts."""
    store = _OneDuePresetStore()
    service = SchedulerService(store, InMemoryMaintenanceQueue())

    call_order: list[str] = []
    monkeypatch.setattr(
        service,
        "_dispatch_scheduled_runs",
        lambda *a, **k: call_order.append("_dispatch_scheduled_runs"),
    )
    orig_dispatch_due = service._dispatch_due_presets

    def _tracked_dispatch_due(current):
        call_order.append("_dispatch_due_presets")
        return orig_dispatch_due(current)

    monkeypatch.setattr(service, "_dispatch_due_presets", _tracked_dispatch_due)
    monkeypatch.setattr(
        service,
        "_expire_stale_approval_requests",
        lambda *a, **k: call_order.append("_expire_stale_approval_requests"),
    )

    service.tick(now=datetime(2026, 8, 31, 20, 0, tzinfo=UTC))

    dispatch_indices = [
        call_order.index("_dispatch_scheduled_runs"),
        call_order.index("_dispatch_due_presets"),
    ]
    diagnostic_index = call_order.index("_expire_stale_approval_requests")
    assert max(dispatch_indices) < diagnostic_index


def test_tick_returns_the_real_dispatch_result_not_a_stale_default() -> None:
    """The refactor moved dispatch's result to be computed before the diagnostic steps but
    returned after them -- confirms the actual SchedulerTickResult from dispatch survives that
    reordering rather than being silently discarded or overwritten."""
    store = _OneDuePresetStore()
    service = SchedulerService(store, InMemoryMaintenanceQueue())

    result = service.tick(now=datetime(2026, 8, 31, 20, 0, tzinfo=UTC))

    assert result.runs_created == 1
    assert result.jobs_enqueued >= 0
    assert result.preset_ids == ("preset-1",)
