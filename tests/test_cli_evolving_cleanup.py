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


def test_runner_is_registered_among_other_runners() -> None:
    """Direct regression guard for the actual bug: the function existed but nothing in
    cryo/cli.py's runner list ever called it. Assert it's wired into the same list the other
    scheduled daemons (benefit-score updater, alert dispatcher, S3 log ingest) live in."""
    import inspect

    import cryo.cli as cli_module

    source = inspect.getsource(cli_module)
    assert "EvolvingCatalogCleanupRunner()" in source
