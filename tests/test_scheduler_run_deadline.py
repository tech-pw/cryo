"""Default deadline for scheduled runs (#383).

Runs were only given a deadline when their preset set guardrails.deadline_minutes, so a run
with one job left deferred (cooldown, approval) stayed open for weeks. A scheduled run now
closes at its preset's next cron fire, held between a floor and a cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from cryo.config import get_settings
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler.cron import next_cron_fire_after
from cryo.scheduler.rules import PresetConfig, PresetRule, TableCandidate
from cryo.scheduler.scheduler import RunDeadlinePolicy, build_scheduled_run
from cryo.scheduler.service import InMemorySchedulerStore, SchedulerService

NOW = datetime(2026, 10, 26, 2, 0, tzinfo=UTC)
_TABLE = TableCandidate("t0", "demo", "t0", {"total_size_gb": 5})
_RULE = {"field": "total_size_gb", "op": ">=", "value": 0}
_POLICY = RunDeadlinePolicy(
    max_duration=timedelta(hours=168), min_duration=timedelta(hours=1), tz=UTC
)


def _preset(cron: str | None = "0 2 * * *", **guardrails: object) -> PresetConfig:
    return PresetConfig(
        id="p1",
        name="p1",
        destination_id="dest-1",
        guardrails=guardrails,
        schedule_cron=cron,
    )


def _deadline(preset: PresetConfig, policy: RunDeadlinePolicy | None = _POLICY, now=NOW):
    rules = [PresetRule("r1", preset.id, _RULE)]
    return build_scheduled_run(
        preset, [_TABLE], [preset], rules, now=now, deadline_policy=policy
    ).deadline_at


class TestNextCronFireAfter:
    def test_returns_the_next_fire_strictly_after_now(self) -> None:
        assert next_cron_fire_after(
            "0 2 * * *", after=NOW, within=timedelta(days=2)
        ) == NOW + timedelta(days=1)

    def test_none_when_no_fire_falls_within_the_window(self) -> None:
        assert next_cron_fire_after("0 2 * * 1", after=NOW, within=timedelta(days=1)) is None

    @pytest.mark.parametrize("expression", [None, "", "not a cron"])
    def test_none_for_missing_or_invalid_expression(self, expression) -> None:
        assert next_cron_fire_after(expression, after=NOW, within=timedelta(days=1)) is None

    def test_matches_in_the_given_zone(self) -> None:
        zone = ZoneInfo("Asia/Kolkata")
        fire = next_cron_fire_after("0 2 * * *", after=NOW, within=timedelta(days=2), tz=zone)
        assert fire == datetime(2026, 10, 27, 2, 0, tzinfo=zone)


class TestDefaultDeadline:
    def test_daily_run_closes_at_the_next_fire(self) -> None:
        assert _deadline(_preset("0 2 * * *")) == NOW + timedelta(days=1)

    def test_late_tick_still_closes_at_the_next_fire(self) -> None:
        late = NOW + timedelta(minutes=25)
        assert _deadline(_preset("0 2 * * *"), now=late) == NOW + timedelta(days=1)

    def test_long_interval_is_capped(self) -> None:
        assert _deadline(_preset("0 2 15 * *")) == NOW + timedelta(hours=168)

    def test_short_interval_is_floored(self) -> None:
        assert _deadline(_preset("*/5 * * * *")) == NOW + timedelta(hours=1)

    def test_floor_never_exceeds_the_cap(self) -> None:
        policy = RunDeadlinePolicy(
            max_duration=timedelta(minutes=30), min_duration=timedelta(hours=1), tz=UTC
        )
        assert _deadline(_preset("*/5 * * * *"), policy) == NOW + timedelta(minutes=30)

    def test_unknown_schedule_uses_the_cap(self) -> None:
        assert _deadline(_preset(cron=None)) == NOW + timedelta(hours=168)

    def test_adhoc_run_uses_the_same_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from cryo.config import get_settings
        from cryo.scheduler.scheduler import default_run_deadline

        monkeypatch.setattr("cryo.display_time.display_zone", lambda: UTC)
        daily = default_run_deadline(schedule_cron="0 2 * * *", guardrails={}, now=NOW)
        assert daily == NOW + timedelta(days=1)
        capped = default_run_deadline(schedule_cron=None, guardrails={}, now=NOW)
        hours = get_settings().scheduled_run_deadline_max_hours
        if hours > 0:
            assert capped == NOW + timedelta(hours=hours)
        else:
            assert capped is None
        assert default_run_deadline(
            schedule_cron=None, guardrails={"deadline_minutes": 90}, now=NOW
        ) == (NOW + timedelta(minutes=90))

    def test_preset_deadline_minutes_wins(self) -> None:
        assert _deadline(_preset(deadline_minutes=90)) == NOW + timedelta(minutes=90)

    def test_disabled_policy_keeps_old_behaviour(self) -> None:
        assert _deadline(_preset(), RunDeadlinePolicy(max_duration=None)) is None
        assert _deadline(_preset(), None) is None

    def test_paused_preset_draft_gets_the_same_deadline(self) -> None:
        paused = PresetConfig(
            id="p1", name="p1", destination_id="dest-1", schedule_cron="0 2 * * *", paused=True
        )
        assert _deadline(paused) == NOW + timedelta(days=1)


class TestSchedulerServiceWiring:
    def _service(self, preset: PresetConfig):
        store = InMemorySchedulerStore()
        store.presets = [preset]
        store.rules = [PresetRule("r1", preset.id, _RULE)]
        store.tables = [_TABLE]
        store.clock = lambda: NOW
        return SchedulerService(store, InMemoryMaintenanceQueue()), store

    def _settings(self, monkeypatch, **update: object) -> None:
        settings = get_settings().model_copy(update={"display_timezone": "UTC", **update})
        monkeypatch.setattr("cryo.config.get_settings", lambda: settings)

    def test_cron_run_is_persisted_with_the_default_deadline(self, monkeypatch) -> None:
        self._settings(monkeypatch)
        service, store = self._service(_preset("0 2 * * *"))

        assert service._dispatch_due_presets(NOW).runs_created == 1

        assert store.runs[0].deadline_at == NOW + timedelta(days=1)

    def test_settings_shape_the_default(self, monkeypatch) -> None:
        self._settings(
            monkeypatch,
            scheduled_run_deadline_max_hours=12,
            scheduled_run_deadline_min_minutes=30,
        )
        service, store = self._service(_preset("0 2 * * *"))

        service._dispatch_due_presets(NOW)

        assert store.runs[0].deadline_at == NOW + timedelta(hours=12)

    def test_zero_max_hours_turns_the_default_off(self, monkeypatch) -> None:
        self._settings(monkeypatch, scheduled_run_deadline_max_hours=0)
        service, store = self._service(_preset("0 2 * * *"))

        service._dispatch_due_presets(NOW)

        assert store.runs[0].deadline_at is None

    def test_default_settings(self) -> None:
        defaults = type(get_settings()).model_fields
        assert defaults["scheduled_run_deadline_max_hours"].default == 168.0
        assert defaults["scheduled_run_deadline_min_minutes"].default == 60
