"""#183: one install time zone for the off-peak gate, cron presets and run report clocks;
#164: benefit-score scan cost accepts PostgreSQL's Decimal averages."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from cryo import display_time
from cryo.governor.admission import IST, is_offpeak
from cryo.planner.benefit_score import _scan_cost_multiplier, compute_benefit_score
from cryo.scheduler.cron import cron_is_due

BERLIN = ZoneInfo("Europe/Berlin")


def _use_zone(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr("cryo.config.get_settings", lambda: SimpleNamespace(display_timezone=name))


def test_offpeak_hours_are_read_in_the_install_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_zone(monkeypatch, "Europe/Berlin")
    # 01:30 Berlin (CEST, UTC+2) in late October is still inside 01-07; 21:30 Berlin is not.
    assert is_offpeak(datetime(2026, 10, 20, 23, 30, tzinfo=UTC), 1, 7)
    assert not is_offpeak(datetime(2026, 10, 20, 19, 30, tzinfo=UTC), 1, 7)


def test_offpeak_defaults_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_zone(monkeypatch, "UTC")
    assert is_offpeak(datetime(2026, 10, 20, 2, 0, tzinfo=UTC), 1, 7)
    # 20:00 UTC was inside 01-07 under the old IST gate (01:30 IST); it is not any more.
    assert not is_offpeak(datetime(2026, 10, 20, 20, 0, tzinfo=UTC), 1, 7)


def test_offpeak_explicit_zone_and_disabled_window() -> None:
    assert is_offpeak(datetime(2026, 10, 20, 20, 0, tzinfo=UTC), 1, 7, tz=IST)
    assert is_offpeak(datetime(2026, 10, 20, 12, 0, tzinfo=UTC), 0, 0)


def test_unknown_zone_falls_back_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    display_time.zone_for.cache_clear()
    _use_zone(monkeypatch, "Mars/Olympus")
    assert display_time.display_zone() is UTC
    assert display_time.display_zone_name() == "UTC"


def test_cron_and_offpeak_agree_in_the_install_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_zone(monkeypatch, "Europe/Berlin")
    now = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)  # 02:00 Berlin
    assert cron_is_due("0 2 * * *", now=now, last_run_at=None, tz=display_time.display_zone())
    assert is_offpeak(now, 1, 7)


def test_scheduler_passes_the_install_zone_to_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    import cryo.scheduler.service as service

    seen: list[object] = []

    def fake_cron_is_due(expression, *, now, last_run_at, tz=None):  # type: ignore[no-untyped-def]
        seen.append(tz)
        return False

    _use_zone(monkeypatch, "Europe/Berlin")
    monkeypatch.setattr(service, "cron_is_due", fake_cron_is_due)
    preset = SimpleNamespace(schedule_cron="0 2 * * *")
    store = SimpleNamespace(
        list_due_presets=lambda current: [(preset, None)],
        list_tables=lambda: [],
        list_presets=lambda: [],
        list_rules=lambda: [],
        list_assignments=lambda: [],
        list_destinations=lambda: [],
    )
    svc = service.SchedulerService.__new__(service.SchedulerService)
    svc._store = store  # type: ignore[attr-defined]
    svc._load_active_coverage = lambda: None  # type: ignore[method-assign]

    svc._dispatch_due_presets(datetime(2026, 10, 20, 0, 0, tzinfo=UTC))

    assert seen == [BERLIN]


def test_run_report_clock_uses_the_install_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryo.maintenance import report

    _use_zone(monkeypatch, "Europe/Berlin")
    assert report._local(datetime(2026, 10, 20, 0, 5, tzinfo=UTC)) == "02:05"


# --- #164 ---------------------------------------------------------------------------------


def test_scan_cost_multiplier_accepts_decimal() -> None:
    # PostgreSQL AVG(bigint) arrives as Decimal; this raised TypeError on every tick.
    value = Decimal("5368709120.0000000000")
    assert _scan_cost_multiplier(value) == _scan_cost_multiplier(float(value))  # type: ignore[arg-type]
    assert _scan_cost_multiplier(Decimal(0)) == 1.0  # type: ignore[arg-type]


def test_benefit_score_with_decimal_average() -> None:
    score = compute_benefit_score(
        small_files=100,
        query_frequency=10.0,
        partition_size_gb=5.0,
        avg_bytes_per_query=Decimal("2147483648"),  # type: ignore[arg-type]
    )
    assert score > 0
