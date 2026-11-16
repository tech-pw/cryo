"""#561: periodic loaders skip tables dropped from the catalog, count them, and hold them
until the next scan reconciles, instead of logging a traceback every tick."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from pyiceberg.exceptions import NoSuchIcebergTableError, NoSuchTableError
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from cryo.db.models import LayoutRecommendation, QueryPattern, Source, Table
from cryo.events.service import hold_missing_watch_target, load_watch_targets
from cryo.events.watcher import InMemorySnapshotProbe, TableCommitWatcher
from cryo.observability.metrics import table_load_skipped_total
from cryo.optimizer.layout_analyzer import fetch_file_stats
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scanner.property_autopilot import run_property_autopilot
from cryo.scheduler.service import SchedulerService
from cryo.sources.missing import (
    TABLE_MISSING_REASON,
    TableMissingFromCatalogError,
    is_held_missing,
    is_missing_table_error,
    note_missing_table,
    table_not_held_missing,
)
from tests.conftest import (
    _assign_missing_autoincrement_primary_keys,
    _assign_missing_uuid_primary_keys,
)


def _skips(component: str) -> float:
    return table_load_skipped_total.labels(component=component, reason="table_missing")._value.get()


def _factory(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    event.listen(factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    return factory


def _seed(session, name: str = "tmp_table") -> Table:
    source = Source(id=uuid.uuid4(), name=f"s-{uuid.uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=name,
        is_active=True,
    )
    session.add(table)
    session.commit()
    return table


class _Wrapped(RuntimeError):
    pass


class _NoSuchTableException(Exception):
    pass


@pytest.mark.parametrize(
    "exc",
    [
        NoSuchTableError("gone"),
        _NoSuchTableException("gone"),
        TableMissingFromCatalogError("gone"),
    ],
)
def test_missing_table_errors_are_recognised(exc: Exception) -> None:
    assert is_missing_table_error(exc)
    wrapped = _Wrapped("outer")
    wrapped.__cause__ = exc
    assert is_missing_table_error(wrapped)


def test_other_errors_are_not_missing() -> None:
    assert not is_missing_table_error(RuntimeError("timeout"))
    assert not is_missing_table_error(NoSuchIcebergTableError("hive table, not iceberg"))
    assert not is_missing_table_error(None)


def test_note_missing_table_holds_counts_and_expires(session, caplog) -> None:
    table = _seed(session)
    before = _skips("layout_analyzer")
    with caplog.at_level(logging.INFO, logger="cryo.sources.missing"):
        note_missing_table(session, table.id, component="layout_analyzer")
    session.refresh(table)

    assert _skips("layout_analyzer") == before + 1
    assert table.maintenance_missing_metadata_reason == TABLE_MISSING_REASON
    assert is_held_missing(table)
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert not any(r.exc_info for r in caplog.records)
    assert session.scalars(select(Table.id).where(table_not_held_missing())).all() == []
    later = datetime.now(UTC) + timedelta(days=2)
    assert not is_held_missing(table, now=later)
    assert session.scalars(select(Table.id).where(table_not_held_missing(later))).all() == [
        table.id
    ]


def test_implicit_context_is_not_followed() -> None:
    try:
        try:
            raise NoSuchTableError("gone")
        except NoSuchTableError:
            raise RuntimeError("auth failed while handling") from None
    except RuntimeError as exc:
        assert not is_missing_table_error(exc)
    try:
        try:
            raise NoSuchTableError("gone")
        except NoSuchTableError:
            raise RuntimeError("auth failed while handling")  # noqa: B904
    except RuntimeError as exc:
        assert exc.__context__ is not None
        assert not is_missing_table_error(exc)


def test_note_missing_table_keeps_an_existing_other_reason(session) -> None:
    table = _seed(session)
    until = datetime.now(UTC) + timedelta(days=30)
    table.maintenance_missing_metadata_reason = "metadata_json_missing"
    table.maintenance_retry_after = until
    session.commit()

    note_missing_table(session, table.id, component="layout_analyzer")
    session.refresh(table)

    assert table.maintenance_missing_metadata_reason == "metadata_json_missing"
    assert table.maintenance_retry_after.replace(tzinfo=UTC) == until


def test_clean_scan_releases_the_missing_hold(session) -> None:
    from cryo.scanner.persistence import record_table_scan_outcome

    table = _seed(session)
    note_missing_table(session, table.id, component="layout_analyzer")
    record_table_scan_outcome(
        session,
        source_id=table.source_id,
        schema_name=table.schema_name,
        table_name=table.table_name,
        error=None,
        scanned_at=datetime.now(UTC),
    )
    session.commit()
    session.refresh(table)

    assert table.maintenance_missing_metadata_reason is None
    assert table.maintenance_retry_after is None
    assert not is_held_missing(table)


def test_clean_scan_keeps_other_holds(session) -> None:
    from cryo.scanner.persistence import record_table_scan_outcome

    table = _seed(session)
    table.maintenance_missing_metadata_reason = "metadata_json_missing"
    table.maintenance_retry_after = datetime.now(UTC) + timedelta(days=30)
    session.commit()
    record_table_scan_outcome(
        session,
        source_id=table.source_id,
        schema_name=table.schema_name,
        table_name=table.table_name,
        error=None,
        scanned_at=datetime.now(UTC),
    )
    session.commit()
    session.refresh(table)

    assert table.maintenance_missing_metadata_reason == "metadata_json_missing"


def test_unheld_tables_pass_the_filter(session) -> None:
    table = _seed(session)
    assert session.scalars(select(Table.id).where(table_not_held_missing())).all() == [table.id]


def test_fetch_file_stats_raises_for_missing_table(session) -> None:
    table = _seed(session)

    class _Catalog:
        def load_table(self, schema: str, name: str) -> object:
            raise NoSuchTableError(f"{schema}.{name}")

    with (
        patch("cryo.sources.factory.source_catalog_for", lambda _source: _Catalog()),
        pytest.raises(TableMissingFromCatalogError),
    ):
        fetch_file_stats(session, table)


def test_fetch_file_stats_still_returns_none_for_other_failures(session) -> None:
    table = _seed(session)

    class _Catalog:
        def load_table(self, schema: str, name: str) -> object:
            raise RuntimeError("metastore down")

    with patch("cryo.sources.factory.source_catalog_for", lambda _source: _Catalog()):
        assert fetch_file_stats(session, table) is None


class _NullStore:
    def __getattr__(self, name):  # pragma: no cover - not exercised here
        raise AssertionError(name)


def _service() -> SchedulerService:
    return SchedulerService(_NullStore(), InMemoryMaintenanceQueue())  # type: ignore[arg-type]


def test_layout_generation_skips_holds_and_does_not_retry(session, monkeypatch, caplog) -> None:
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", _factory(session))
    gone = _seed(session, "gone")
    kept = _seed(session, "kept")
    session.add_all(
        [
            QueryPattern(table_id=gone.id, column_name="id", filter_count=1),
            QueryPattern(table_id=kept.id, column_name="id", filter_count=1),
        ]
    )
    session.commit()
    calls: list[uuid.UUID] = []

    def _generate(_session, table_id, force_refresh=False):
        calls.append(table_id)
        if table_id == gone.id:
            raise TableMissingFromCatalogError("gone")

    monkeypatch.setattr("cryo.optimizer.layout_analyzer.generate_layout_recommendation", _generate)
    settings = type("S", (), {"layout_recommendation_max_tables_per_tick": 10})()
    before = _skips("layout_analyzer")

    with caplog.at_level(logging.INFO):
        _service()._generate_layout_recommendations(settings)
    assert set(calls) == {gone.id, kept.id}
    assert _skips("layout_analyzer") == before + 1
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    calls.clear()
    _service()._generate_layout_recommendations(settings)
    assert calls == [kept.id]


def test_layout_generation_skips_tombstoned_tables(session, monkeypatch) -> None:
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", _factory(session))
    gone = _seed(session, "gone")
    gone.is_active = False
    gone.removed_from_catalog_at = datetime.now(UTC)
    session.add(QueryPattern(table_id=gone.id, column_name="id", filter_count=1))
    session.commit()
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(
        "cryo.optimizer.layout_analyzer.generate_layout_recommendation",
        lambda _s, table_id, force_refresh=False: calls.append(table_id),
    )
    settings = type("S", (), {"layout_recommendation_max_tables_per_tick": 10})()
    _service()._generate_layout_recommendations(settings)
    assert calls == []


def test_layout_effectiveness_skips_and_holds(session, monkeypatch) -> None:
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", _factory(session))
    gone = _seed(session, "gone")
    session.add(
        LayoutRecommendation(
            id=uuid.uuid4(),
            table_id=gone.id,
            recommended_strategy="sort",
            recommended_columns=["id"],
            status="applied",
            applied_at=datetime.now(UTC) - timedelta(days=7),
            applied_prune_efficiency_baseline=0.1,
        )
    )
    session.commit()
    calls: list[object] = []

    def _measure(_session, recommendation):
        calls.append(recommendation.id)
        raise TableMissingFromCatalogError("gone")

    monkeypatch.setattr(
        "cryo.optimizer.layout_analyzer.measure_layout_recommendation_effectiveness", _measure
    )
    settings = type("S", (), {"layout_effectiveness_max_tables_per_tick": 10})()
    before = _skips("layout_effectiveness")
    _service()._measure_layout_recommendation_effectiveness(settings)
    _service()._measure_layout_recommendation_effectiveness(settings)
    assert len(calls) == 1
    assert _skips("layout_effectiveness") == before + 1


def test_property_autopilot_skips_and_holds_missing_tables(session) -> None:
    _seed(session, "gone")
    before = _skips("property_autopilot")
    with patch(
        "cryo.scanner.property_autopilot.compute_property_recommendations",
        side_effect=NoSuchTableError("gone"),
    ) as compute:
        first = run_property_autopilot(session)
        second = run_property_autopilot(session)
    assert first.tables_failed == 0
    assert first.tables_considered == 1
    assert second.tables_considered == 0
    assert compute.call_count == 1
    assert _skips("property_autopilot") == before + 1


def test_commit_watcher_unwatches_and_holds_missing_table() -> None:
    class _Probe(InMemorySnapshotProbe):
        def current_snapshot_id(self, table_ref: str) -> int | None:
            raise NoSuchTableError(table_ref)

    held: list[str] = []
    watcher = TableCommitWatcher(_Probe({}), lambda _e: None, on_missing=held.append)
    watcher.watch("analytics.gone", table_id="tid")
    assert watcher.poll_once() == []
    assert held == ["tid"]
    assert "analytics.gone" not in watcher.watched


def test_commit_watcher_without_hook_still_unwatches() -> None:
    class _Probe(InMemorySnapshotProbe):
        def current_snapshot_id(self, table_ref: str) -> int | None:
            raise NoSuchTableError(table_ref)

    watcher = TableCommitWatcher(_Probe({}), lambda _e: None)
    watcher.watch("analytics.gone")
    before = _skips("commit_watcher")
    watcher.poll_once()
    assert watcher.watched == frozenset()
    assert _skips("commit_watcher") == before + 1


def test_hold_missing_watch_target_drops_it_from_targets(session) -> None:
    factory = _factory(session)
    table = _seed(session, "streaming")
    from cryo.db.models import TableStats

    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=95,
            total_files=1,
            small_files_count=0,
            total_size_gb=1.0,
            avg_file_size_mb=1.0,
            total_partitions=1,
            total_snapshots=1,
            total_delete_files=0,
            delete_file_pct=0.0,
            raw={"is_streaming": True},
        )
    )
    session.commit()
    with patch("cryo.events.service.continuous_table_ids", lambda _f: []):
        assert [t.table_id for t in load_watch_targets(factory)] == [str(table.id)]
        hold_missing_watch_target(factory, str(table.id))
        assert load_watch_targets(factory) == []
