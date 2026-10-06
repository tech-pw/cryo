"""Tests for CostModelRefreshRunner (cryo/cli.py) -- the scheduled wiring for
refresh_cost_model_cache, which used to run inline on every single worker job completion.

Live outage, 2026-09-10: confirmed live as the real root cause of a fleet-wide worker OOM
cascade (kernel-verified via /proc/1/smaps_rollup: Anonymous heap climbed ~1GB per ~6-10s
until OOMKilled, always immediately after a job's last operation finished) and independently
reproduced by OOMKilling a cryo-api pod via a single call to /api/v1/ai/cost-model/evaluate
(same build_outcome_dataset code path). Moved to a periodic daemon, matching
EvolvingCatalogCleanupRunner's own fix shape (tests/test_cli_evolving_cleanup.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

from cryo.cli import CostModelRefreshRunner


def test_runner_uses_configured_interval(monkeypatch) -> None:
    settings = MagicMock(cost_model_refresh_interval_hours=3)
    monkeypatch.setattr("cryo.cli.get_settings", lambda: settings)

    runner = CostModelRefreshRunner()

    assert runner.interval == 3 * 3600


def test_runner_explicit_interval_overrides_settings() -> None:
    runner = CostModelRefreshRunner(interval=42)

    assert runner.interval == 42


def test_tick_logs_when_model_refreshed(monkeypatch) -> None:
    session = MagicMock()
    session_local = MagicMock()
    session_local.return_value.__enter__.return_value = session
    monkeypatch.setattr("cryo.db.session.SessionLocal", session_local)
    refresh = MagicMock(return_value=True)
    monkeypatch.setattr("cryo.ml.cost_integration.refresh_cost_model_cache", refresh)

    runner = CostModelRefreshRunner(interval=3600)
    runner._logger = MagicMock()

    runner._tick()

    refresh.assert_called_once()
    assert refresh.call_args.args[0] is session
    runner._logger.info.assert_called_once()


def test_tick_stays_silent_when_no_model_produced(monkeypatch) -> None:
    session = MagicMock()
    session_local = MagicMock()
    session_local.return_value.__enter__.return_value = session
    monkeypatch.setattr("cryo.db.session.SessionLocal", session_local)
    refresh = MagicMock(return_value=False)
    monkeypatch.setattr("cryo.ml.cost_integration.refresh_cost_model_cache", refresh)

    runner = CostModelRefreshRunner(interval=3600)
    runner._logger = MagicMock()

    runner._tick()

    refresh.assert_called_once()
    runner._logger.info.assert_not_called()


def test_runner_is_wired_into_run_scheduler() -> None:
    """Direct regression guard: assert the daemon is specifically inside run_scheduler's own
    source, alongside its sibling daemons -- not just "referenced somewhere in the module",
    which would pass even if it were wired into the wrong place (run_serve's other_runners,
    which no Helm template actually starts -- see EvolvingCatalogCleanupRunner's own history
    of this exact mistake)."""
    import inspect

    from cryo.cli import run_scheduler

    source = inspect.getsource(run_scheduler)
    assert "CostModelRefreshRunner()" in source
    assert "cost_model_refresh.stop()" in source


def test_refresh_cost_model_cache_not_called_from_worker_hot_path() -> None:
    """Regression guard for the actual outage: refresh_cost_model_cache must not be
    reachable from _process_claimed_job's per-job completion path -- that inline call is
    exactly what OOMKilled the fleet. Source-level check (not a call-count mock) so this
    fails even if someone re-adds the call under a different import alias. Checks for an
    actual call/import, not just the string anywhere (the explanatory comment left in place
    mentions the name deliberately, as history -- that must not fail this test)."""
    import inspect

    from cryo.worker.worker import Worker

    source = inspect.getsource(Worker._process_claimed_job)
    assert "refresh_cost_model_cache(" not in source
    assert "import refresh_cost_model_cache" not in source
