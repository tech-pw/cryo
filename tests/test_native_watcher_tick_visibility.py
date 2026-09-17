"""Regression coverage for a real incident, 2026-09-15/16: GovernorRunner._tick_native_watcher
swallowed every exception from NativeJobWatcher.tick() at logger.debug (invisible in prod --
configure_logging sets INFO there per cli.py's module-level setup), with no metric and no
retry-vs-give-up distinction. A run of transient K8s API failures (matching the same window's
metastore/cluster congestion) silently skipped every tick with zero signal, leaving completed
native-engine K8s Jobs' MaintenanceJob rows stuck at status="running" indefinitely -- which then
fed straight into native_admit.py's metastore-capacity headroom check as phantom load (confirmed
live: 164+ Completed native pods vs. 11 actually Running, but the DB still counted 258 as
"running").
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from cryo.cli import GovernorRunner
from cryo.observability.metrics import native_watcher_tick_failures_total


def _sample(exception_type: str) -> float:
    try:
        return native_watcher_tick_failures_total.labels(exception_type=exception_type)._value.get()
    except Exception:
        return 0.0


def test_watcher_tick_failure_is_logged_and_counted_not_swallowed() -> None:
    runner = GovernorRunner()
    wiring = SimpleNamespace(lock_store=None, job_store=SimpleNamespace(mark_status=None, record_audit=None))
    before = _sample("RuntimeError")

    with patch("cryo.engines.native.watcher.NativeJobWatcher") as mock_watcher_cls:
        mock_watcher_cls.return_value.tick.side_effect = RuntimeError("k8s api unreachable")
        with patch.object(runner._logger, "warning") as mock_warning:
            runner._tick_native_watcher(wiring)

    assert mock_warning.called, "a tick failure must be logged at warning (visible in prod), not debug"
    assert runner._consecutive_native_watcher_failures == 1
    assert _sample("RuntimeError") == before + 1.0


def test_repeated_watcher_tick_failures_escalate_to_error() -> None:
    """A single blip logs at warning; a sustained run of failures (the actual incident shape --
    hours of transient K8s API errors during metastore congestion) must escalate to error so it
    surfaces above routine noise."""
    runner = GovernorRunner()
    wiring = SimpleNamespace(lock_store=None, job_store=SimpleNamespace(mark_status=None, record_audit=None))

    with patch("cryo.engines.native.watcher.NativeJobWatcher") as mock_watcher_cls:
        mock_watcher_cls.return_value.tick.side_effect = RuntimeError("k8s api unreachable")
        with patch.object(runner._logger, "warning") as mock_warning, patch.object(
            runner._logger, "error"
        ) as mock_error:
            for _ in range(5):
                runner._tick_native_watcher(wiring)

    assert runner._consecutive_native_watcher_failures == 5
    assert mock_error.called, "the 5th consecutive failure must escalate to error"
    # The first 4 went to warning, not error.
    assert mock_warning.call_count == 4


def test_watcher_tick_success_resets_the_consecutive_failure_counter() -> None:
    runner = GovernorRunner()
    wiring = SimpleNamespace(lock_store=None, job_store=SimpleNamespace(mark_status=None, record_audit=None))
    runner._consecutive_native_watcher_failures = 3

    with patch("cryo.engines.native.watcher.NativeJobWatcher") as mock_watcher_cls:
        mock_watcher_cls.return_value.tick.return_value = SimpleNamespace(
            finalized=0, still_running=0, errors=0
        )
        runner._tick_native_watcher(wiring)

    assert runner._consecutive_native_watcher_failures == 0
