"""Tests for EvolvingCatalogCleanupRunner (cryo/cli.py) -- the scheduled wiring for
deactivate_orphaned_evolving_catalog_entries, which existed since 2026-08-26 (ace7f4b) but was
never actually called from a running daemon, letting the fleet's single worst repeat_failures
pattern (orphaned '*_evolving' catalog rows) keep recurring indefinitely."""

from __future__ import annotations

from unittest.mock import MagicMock

from cryo.cli import EvolvingCatalogCleanupRunner


def test_runner_uses_configured_interval(monkeypatch) -> None:
    settings = MagicMock(evolving_catalog_cleanup_interval_hours=3)
    monkeypatch.setattr("cryo.cli.get_settings", lambda: settings)

    runner = EvolvingCatalogCleanupRunner()

    assert runner.interval == 3 * 3600


def test_runner_explicit_interval_overrides_settings() -> None:
    runner = EvolvingCatalogCleanupRunner(interval=42)

    assert runner.interval == 42


def test_tick_commits_and_logs_when_rows_deactivated(monkeypatch) -> None:
    session = MagicMock()
    session_local = MagicMock()
    session_local.return_value.__enter__.return_value = session
    monkeypatch.setattr("cryo.db.session.SessionLocal", session_local)
    deactivate = MagicMock(return_value=7)
    monkeypatch.setattr(
        "cryo.db.repositories.sources.deactivate_orphaned_evolving_catalog_entries", deactivate
    )

    runner = EvolvingCatalogCleanupRunner(interval=3600)
    runner._logger = MagicMock()

    runner._tick()

    deactivate.assert_called_once_with(session)
    session.commit.assert_called_once_with()
    runner._logger.info.assert_called_once()


def test_tick_commits_silently_when_nothing_to_deactivate(monkeypatch) -> None:
    session = MagicMock()
    session_local = MagicMock()
    session_local.return_value.__enter__.return_value = session
    monkeypatch.setattr("cryo.db.session.SessionLocal", session_local)
    deactivate = MagicMock(return_value=0)
    monkeypatch.setattr(
        "cryo.db.repositories.sources.deactivate_orphaned_evolving_catalog_entries", deactivate
    )

    runner = EvolvingCatalogCleanupRunner(interval=3600)
    runner._logger = MagicMock()

    runner._tick()

    session.commit.assert_called_once_with()
    runner._logger.info.assert_not_called()


def test_runner_is_wired_into_run_scheduler() -> None:
    """Direct regression guard for the actual bug: deactivate_orphaned_evolving_catalog_entries
    (ace7f4b, 2026-08-26) existed and was tested but never actually ran in production. It was
    first wired into run_serve's other_runners list -- but no Helm template starts "cryo serve"
    (deploy/helm/cryo/templates/scheduler-deployment.yaml runs "cryo scheduler" ->
    run_scheduler(), and api-deployment.yaml runs plain uvicorn with no CLI daemons at all), so
    that wiring never actually ran either. Assert it specifically inside run_scheduler's own
    source, alongside its sibling daemons (benefit-score updater, alert dispatcher, S3 log
    ingest) that this same function already starts as threads -- not just "referenced
    somewhere in the module", which would have passed even with the original wrong wiring."""
    import inspect

    from cryo.cli import run_scheduler

    source = inspect.getsource(run_scheduler)
    assert "EvolvingCatalogCleanupRunner()" in source
    assert "evolving_cleanup.stop()" in source
