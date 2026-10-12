"""Tests for SchedulerService._maybe_check_unmatched_tables -- the fleet-wide sweep that
surfaces active tables resolving to no effective preset (never scheduled by any cron).

See cryo/scheduler/rules.py's effective_preset() and the "no platform-default fallback by
design" comment there: this sweep doesn't change that behavior, it just makes the resulting
gap visible instead of silent. Real incident, 2026-08-12: 1,267 active tables with
needs_compaction=True matched no enabled preset, found only via manual investigation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler.rules import PresetConfig, PresetRule, TableAssignment, TableCandidate
from cryo.scheduler.service import SchedulerService


class _FakeStore:
    def __init__(
        self,
        tables: list[TableCandidate],
        presets: list[PresetConfig],
        rules: list[PresetRule],
        assignments: list[TableAssignment] | None = None,
    ) -> None:
        self._tables = tables
        self._presets = presets
        self._rules = rules
        self._assignments = assignments or []

    def list_due_presets(self, now):
        return []

    def list_tables(self):
        return self._tables

    def list_presets(self):
        return self._presets

    def list_rules(self):
        return self._rules

    def list_assignments(self):
        return self._assignments

    def list_destinations(self):
        return []

    def persist_run(self, run):
        pass

    def mark_preset_run(self, preset_id, run_at):
        pass


def _service(store: _FakeStore) -> SchedulerService:
    return SchedulerService(store, InMemoryMaintenanceQueue())


def test_unmatched_tables_sweep_sets_zero_gauges_when_everything_matches() -> None:
    from cryo.observability.metrics import scheduler_unmatched_tables

    preset = PresetConfig("standard", "standard", "trino", priority=100)
    rules = [PresetRule("r1", "standard", {"field": "total_size_gb", "op": ">=", "value": 0})]
    tables = [TableCandidate("t1", "demo", "orders", {"total_size_gb": 5})]

    service = _service(_FakeStore(tables, [preset], rules))
    service._maybe_check_unmatched_tables(datetime.now(UTC))

    assert scheduler_unmatched_tables.labels(needs_compaction="true")._value.get() == 0
    assert scheduler_unmatched_tables.labels(needs_compaction="false")._value.get() == 0


def test_unmatched_tables_sweep_counts_needs_compaction_separately() -> None:
    from cryo.observability.metrics import scheduler_unmatched_tables

    # No presets/rules at all -- every table is unmatched, split by needs_compaction.
    tables = [
        TableCandidate("t1", "demo", "hot_table", {"total_size_gb": 300, "needs_compaction": True}),
        TableCandidate(
            "t2", "demo", "cold_table", {"total_size_gb": 10, "needs_compaction": False}
        ),
        TableCandidate(
            "t3", "demo", "another_hot", {"total_size_gb": 500, "needs_compaction": True}
        ),
    ]

    service = _service(_FakeStore(tables, [], []))
    service._maybe_check_unmatched_tables(datetime.now(UTC))

    assert scheduler_unmatched_tables.labels(needs_compaction="true")._value.get() == 2
    assert scheduler_unmatched_tables.labels(needs_compaction="false")._value.get() == 1


def test_unmatched_tables_sweep_respects_interval_gate() -> None:
    from cryo.config import get_settings

    tables = [TableCandidate("t1", "demo", "hot", {"total_size_gb": 300, "needs_compaction": True})]
    service = _service(_FakeStore(tables, [], []))

    interval_hours = get_settings().unmatched_tables_check_interval_hours
    first = datetime.now(UTC)
    service._maybe_check_unmatched_tables(first)
    assert service._last_unmatched_tables_check == first

    # Well within the interval -- should be a no-op, timestamp unchanged.
    from datetime import timedelta

    second = first + timedelta(minutes=1)
    service._maybe_check_unmatched_tables(second)
    assert service._last_unmatched_tables_check == first

    # Past the interval -- should run again.
    third = first + timedelta(hours=interval_hours, minutes=1)
    service._maybe_check_unmatched_tables(third)
    assert service._last_unmatched_tables_check == third


def test_unmatched_tables_sweep_survives_store_errors() -> None:
    class _BrokenStore(_FakeStore):
        def list_tables(self):
            raise RuntimeError("db unavailable")

    service = _service(_BrokenStore([], [], []))
    # Must not raise -- a failed sweep should not take down the scheduler tick.
    service._maybe_check_unmatched_tables(datetime.now(UTC))
    assert service._last_unmatched_tables_check is None
