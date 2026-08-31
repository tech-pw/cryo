"""Tests for cryo/scheduler/service.py's _maybe_check_ml_drift timeout bound.

Regression coverage for a real production incident, 2026-08-31: detect_all_anomalies runs 2+
unbatched, sequential DB queries per table with recent scan history (4,287 tables observed
live) -- fine at a smaller fleet size, but confirmed live to exceed 5 minutes at this scale,
with no timeout of its own. Since _maybe_check_ml_drift is the *first* step in tick() (called
before _dispatch_scheduled_runs), and self._last_ml_drift_check only got set *after* a
successful completion, a single slow/hung call meant it never updated -- so every subsequent
tick re-attempted the same expensive scan from scratch, forever, never reaching dispatch even
once. Confirmed live: zero new MaintenanceRun rows created for days despite the scheduler pod
staying healthy and cron_is_due (fixed earlier the same day) correctly reporting every preset
as due on every single tick -- the loop never got that far.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler import service as service_module
from cryo.scheduler.service import SchedulerService


class _NullStore:
    def list_due_presets(self, now):
        return []

    def list_tables(self):
        return []

    def list_presets(self):
        return []

    def list_rules(self):
        return []

    def list_assignments(self):
        return []

    def list_destinations(self):
        return []


def test_hung_drift_scan_times_out_instead_of_blocking_forever(session, monkeypatch) -> None:
    """Direct regression test for the live incident: a scan that never returns must not block
    tick() indefinitely -- it must give up after _ML_DRIFT_CHECK_TIMEOUT_SECONDS."""

    def _hanging_scan() -> dict[str, object]:
        # Long enough to comfortably outlast the patched timeout below, short enough that the
        # shared module-level executor (a real ThreadPoolExecutor -- a timed-out call is
        # abandoned, not cancelled, per this module's own docstring) doesn't serialize this
        # test suite behind multiple slow background threads.
        time.sleep(2)
        raise AssertionError("should have timed out long before this returned")

    monkeypatch.setattr(service_module, "_run_ml_drift_scan", _hanging_scan)
    monkeypatch.setattr(service_module, "_ML_DRIFT_CHECK_TIMEOUT_SECONDS", 0.2)

    service = SchedulerService(_NullStore(), InMemoryMaintenanceQueue())
    now = datetime.now(UTC)

    started = time.monotonic()
    service._maybe_check_ml_drift(now)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"_maybe_check_ml_drift blocked for {elapsed:.1f}s instead of timing out"


def test_timeout_still_records_last_check_so_it_does_not_retry_every_tick(
    session, monkeypatch
) -> None:
    """The critical part of the fix: a timeout must still set _last_ml_drift_check, exactly
    like a success would -- otherwise every subsequent tick re-attempts the same slow/hanging
    scan forever, which is the actual live incident (not just "one tick was slow")."""

    def _hanging_scan() -> dict[str, object]:
        time.sleep(30)
        raise AssertionError("should have timed out long before this returned")

    monkeypatch.setattr(service_module, "_run_ml_drift_scan", _hanging_scan)
    monkeypatch.setattr(service_module, "_ML_DRIFT_CHECK_TIMEOUT_SECONDS", 0.2)

    service = SchedulerService(_NullStore(), InMemoryMaintenanceQueue())
    now = datetime.now(UTC)

    assert service._last_ml_drift_check is None
    service._maybe_check_ml_drift(now)
    assert service._last_ml_drift_check == now


def test_second_tick_within_interval_skips_without_calling_the_scan_again(
    session, monkeypatch
) -> None:
    """Follows directly from the previous test: once _last_ml_drift_check is set (even by a
    timeout), a tick soon after must not re-invoke the scan at all until the configured
    interval has elapsed -- confirms the timeout's bookkeeping actually prevents the
    every-tick-retries-forever failure mode, not just that the field gets set once."""
    call_count = 0

    def _counting_scan() -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        return {"anomalies": 0}

    monkeypatch.setattr(service_module, "_run_ml_drift_scan", _counting_scan)

    service = SchedulerService(_NullStore(), InMemoryMaintenanceQueue())
    first_tick = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)
    service._maybe_check_ml_drift(first_tick)
    assert call_count == 1

    one_minute_later = first_tick + timedelta(minutes=1)
    service._maybe_check_ml_drift(one_minute_later)
    assert call_count == 1  # still not due again -- interval hasn't elapsed


def test_fast_scan_still_completes_normally_and_reports_results(session, monkeypatch) -> None:
    """Baseline: a scan that returns quickly must behave exactly as before this fix -- the
    timeout bound must not change outcomes for the common, fast case."""

    def _fast_scan() -> dict[str, object]:
        return {"anomalies": 3}

    monkeypatch.setattr(service_module, "_run_ml_drift_scan", _fast_scan)

    service = SchedulerService(_NullStore(), InMemoryMaintenanceQueue())
    now = datetime.now(UTC)
    service._maybe_check_ml_drift(now)

    assert service._last_ml_drift_check == now


def test_real_scan_body_still_runs_end_to_end_through_the_bounded_wrapper(
    session, monkeypatch
) -> None:
    """Confirms _run_ml_drift_scan itself (not a mock) still executes correctly through the new
    executor-bound call path -- the SessionLocal patch must actually reach the thread the
    executor runs it on, same pattern test_scheduler_health.py's sweep test already
    establishes for an identical cross-thread-session concern."""
    scoped_factory = sessionmaker(bind=session.get_bind())
    from tests.conftest import _assign_missing_uuid_primary_keys

    event.listen(scoped_factory, "before_flush", _assign_missing_uuid_primary_keys)
    monkeypatch.setattr(service_module, "SessionLocal", scoped_factory)

    service = SchedulerService(_NullStore(), InMemoryMaintenanceQueue())
    now = datetime.now(UTC)

    # No tables seeded -- detect_all_anomalies should cleanly find nothing rather than error,
    # and the whole call must complete well within the real (unpatched) timeout.
    started = time.monotonic()
    service._maybe_check_ml_drift(now)
    elapsed = time.monotonic() - started

    assert elapsed < service_module._ML_DRIFT_CHECK_TIMEOUT_SECONDS
    assert service._last_ml_drift_check == now
