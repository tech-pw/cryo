"""Regression coverage for cryo/telemetry/iceberg_source.py's memory-bounded scan.

The production bug: `_read_query_texts` used to do one unbounded `.scan(...).to_arrow()` over
the whole lookback window (up to 7 days on first run). Against a busy Trino cluster's
query-event-listener table -- which can log millions of rows, each with a potentially large raw
SQL text column -- that single scan OOM-killed the process reading it. The fix chunks the scan
into fixed time windows with a per-window row cap, persisting the watermark after each window so
a bounded or interrupted run never silently skips data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.config import get_settings
from cryo.db.models import Source, Table, TableQueryActivity, TableStats
from cryo.telemetry import iceberg_source
from cryo.telemetry.iceberg_source import (
    IcebergQueryLogConfig,
    _read_query_texts,
    aggregate_from_iceberg_query_log,
)


def _patch_window_hours(monkeypatch, hours: int) -> None:
    """Pin query_log_iceberg_window_hours for a test, independent of the tuned default."""
    settings = get_settings().model_copy(update={"query_log_iceberg_window_hours": hours})
    monkeypatch.setattr("cryo.config.get_settings", lambda: settings)


def _seed_source_and_table(
    session, *, schema_name: str = "s", table_name: str = "t"
) -> tuple[Source, Table]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="hive", config={"uri": "x"})
    session.add(source)
    session.flush()
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name=schema_name,
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.flush()
    return source, table


# ---------------------------------------------------------------------------
# _read_query_texts: batching + row-cap truncation
# ---------------------------------------------------------------------------


class _FakeColumn:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def to_pylist(self) -> list[str]:
        return self._values


class _FakeBatch:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def column(self, name: str) -> _FakeColumn:
        assert name == "query"
        return _FakeColumn(self._texts)


class _FakeScan:
    def __init__(self, batches: list[list[str]]) -> None:
        self._batches = batches

    def to_arrow_batch_reader(self):
        return iter(_FakeBatch(batch) for batch in self._batches)


class _FakeIcebergTable:
    def __init__(self, batches: list[list[str]]) -> None:
        self._batches = batches

    def scan(self, *, row_filter, selected_fields):
        assert selected_fields == ("query", "query_date")
        return _FakeScan(self._batches)


class _FakeCatalog:
    def __init__(self, table: _FakeIcebergTable) -> None:
        self._table = table

    def load_table(self, namespace: str, table_name: str):
        return self._table


def _config() -> IcebergQueryLogConfig:
    return IcebergQueryLogConfig(table_identifier="ns.t", executed_at_column="query_date")


def test_read_query_texts_reads_across_multiple_batches(monkeypatch) -> None:
    fake_table = _FakeIcebergTable(batches=[["select 1"], ["select 2", "select 3"]])
    monkeypatch.setattr(
        "cryo.sources.factory.source_catalog_for", lambda source: _FakeCatalog(fake_table)
    )

    texts, truncated = _read_query_texts(
        Source(id=uuid4(), name="s", type="hive", config={}),
        _config(),
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=datetime(2026, 1, 1, 1, tzinfo=UTC),
        max_rows=100,
    )

    assert texts == ["select 1", "select 2", "select 3"]
    assert truncated is False


def test_read_query_texts_stops_at_max_rows(monkeypatch) -> None:
    fake_table = _FakeIcebergTable(batches=[["a", "b", "c"], ["d", "e"]])
    monkeypatch.setattr(
        "cryo.sources.factory.source_catalog_for", lambda source: _FakeCatalog(fake_table)
    )

    texts, truncated = _read_query_texts(
        Source(id=uuid4(), name="s", type="hive", config={}),
        _config(),
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=datetime(2026, 1, 1, 1, tzinfo=UTC),
        max_rows=2,
    )

    assert texts == ["a", "b"]
    assert truncated is True


# ---------------------------------------------------------------------------
# aggregate_from_iceberg_query_log: windowing + watermark progression
# ---------------------------------------------------------------------------


def test_aggregate_chunks_backlog_into_windows_and_advances_watermark(session, monkeypatch) -> None:
    source, table = _seed_source_and_table(session, schema_name="s", table_name="t")
    now = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
    # No prior watermark -> since = now - 7 days. window_hours default is 6h, so this backlog
    # spans far more than max_windows_per_tick (8) windows; only the first 8 should be processed
    # in a single call, proving a large backlog doesn't get read in one unbounded pass.
    source.config = {
        "query_log": {"mode": "iceberg_table", "table": "s.t", "executed_at_column": "query_date"}
    }
    session.commit()

    calls: list[tuple[datetime, datetime]] = []

    def _fake_read(src, cfg, *, since, until, max_rows):
        calls.append((since, until))
        return ([f"select * from s.t where x = {len(calls)}"], False)

    monkeypatch.setattr(iceberg_source, "_read_query_texts", _fake_read)
    # _upsert_query_patterns uses a Postgres-only on_conflict_do_update; the test session is
    # sqlite, and this test's focus is the windowing/watermark loop, not that unrelated upsert.
    monkeypatch.setattr(iceberg_source, "_upsert_query_patterns", lambda session, stats: len(stats))

    updated = aggregate_from_iceberg_query_log(session, source, now=now)

    from cryo.config import get_settings

    settings = get_settings()
    assert len(calls) == settings.query_log_iceberg_max_windows_per_tick
    assert updated > 0

    session.refresh(source)
    persisted_watermark = source.config["query_log"]["last_aggregated_at"]
    # Watermark should have advanced to the end of the last *processed* window, not to `now` --
    # the backlog wasn't fully drained in this one call.
    assert datetime.fromisoformat(persisted_watermark) < now
    assert datetime.fromisoformat(persisted_watermark) == calls[-1][1]


def test_aggregate_resumes_from_watermark_on_next_call(session, monkeypatch) -> None:
    _patch_window_hours(monkeypatch, 6)
    source, table = _seed_source_and_table(session, schema_name="s", table_name="t")
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    last_aggregated = now - timedelta(hours=3)
    source.config = {
        "query_log": {
            "mode": "iceberg_table",
            "table": "s.t",
            "executed_at_column": "query_date",
            "last_aggregated_at": last_aggregated.isoformat(),
        }
    }
    session.commit()

    calls: list[tuple[datetime, datetime]] = []

    def _fake_read(src, cfg, *, since, until, max_rows):
        calls.append((since, until))
        return ([], False)

    monkeypatch.setattr(iceberg_source, "_read_query_texts", _fake_read)

    aggregate_from_iceberg_query_log(session, source, now=now)

    # Backlog is only 3h with a 6h window -> exactly one window, ending at `now`.
    assert calls == [(last_aggregated, now)]
    session.refresh(source)
    assert source.config["query_log"]["last_aggregated_at"] == now.isoformat()


def test_aggregate_matches_queries_to_tables_across_windows(session, monkeypatch) -> None:
    _patch_window_hours(monkeypatch, 6)
    source, table = _seed_source_and_table(session, schema_name="s", table_name="t")
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    source.config = {
        "query_log": {
            "mode": "iceberg_table",
            "table": "s.t",
            "executed_at_column": "query_date",
            "last_aggregated_at": (now - timedelta(hours=6)).isoformat(),
        }
    }
    session.commit()

    monkeypatch.setattr(
        iceberg_source,
        "_read_query_texts",
        lambda src, cfg, *, since, until, max_rows: (
            ["select a from s.t where a = 1"],
            False,
        ),
    )

    captured: dict = {}

    def _fake_upsert(session, table_stats):
        captured.update(table_stats)
        return len(table_stats)

    # _upsert_query_patterns uses a Postgres-only on_conflict_do_update; the test session is
    # sqlite, so assert on what this function builds and hands off, not the SQL it issues.
    monkeypatch.setattr(iceberg_source, "_upsert_query_patterns", _fake_upsert)

    updated = aggregate_from_iceberg_query_log(session, source, now=now)

    assert updated >= 1
    assert table.id in captured
    assert captured[table.id]["a"].filter_count == 1


def test_aggregate_populates_query_count_30d_end_to_end(session, monkeypatch) -> None:
    """Regression, 2026-07-31: TableStats.query_count_30d was stuck at 0 for every table
    fleet-wide -- query_patterns (per-column filter/join/projection counts) was never enough
    to answer "how many queries touched this table", and nothing wrote a real count anywhere
    else. This is the actual production path (source.config.query_log.mode=iceberg_table) that
    must now produce a TableQueryActivity row and refresh TableStats.query_count_30d."""
    _patch_window_hours(monkeypatch, 6)
    source, table = _seed_source_and_table(session, schema_name="s", table_name="t")
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime(2026, 1, 1, tzinfo=UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="C",
            overall_score=65,
            total_files=10,
            small_files_count=1,
            total_size_gb=1.0,
            query_count_30d=0,
        )
    )
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    source.config = {
        "query_log": {
            "mode": "iceberg_table",
            "table": "s.t",
            "executed_at_column": "query_date",
            "last_aggregated_at": (now - timedelta(hours=6)).isoformat(),
        }
    }
    session.commit()

    monkeypatch.setattr(
        iceberg_source,
        "_read_query_texts",
        lambda src, cfg, *, since, until, max_rows: (
            ["select a from s.t where a = 1", "select b from s.t where b = 2"],
            False,
        ),
    )
    # _upsert_query_patterns uses a Postgres-only on_conflict_do_update; the test session is
    # sqlite, and this test is about query_count_30d, not that unrelated upsert.
    monkeypatch.setattr(iceberg_source, "_upsert_query_patterns", lambda session, stats: len(stats))

    aggregate_from_iceberg_query_log(session, source, now=now)

    activity = session.query(TableQueryActivity).filter_by(table_id=table.id, day=now.date()).one()
    assert activity.query_count == 2
    assert session.get(TableStats, table.id).query_count_30d == 2


def test_aggregate_stops_early_on_read_failure_without_losing_watermark_progress(
    session, monkeypatch
) -> None:
    _patch_window_hours(monkeypatch, 6)
    source, table = _seed_source_and_table(session, schema_name="s", table_name="t")
    now = datetime(2026, 1, 1, 18, 0, tzinfo=UTC)
    source.config = {
        "query_log": {
            "mode": "iceberg_table",
            "table": "s.t",
            "executed_at_column": "query_date",
            "last_aggregated_at": (now - timedelta(hours=12)).isoformat(),
        }
    }
    session.commit()

    call_count = 0

    def _fake_read(src, cfg, *, since, until, max_rows):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("catalog unreachable")
        return ([], False)

    monkeypatch.setattr(iceberg_source, "_read_query_texts", _fake_read)

    aggregate_from_iceberg_query_log(session, source, now=now)

    session.refresh(source)
    persisted = datetime.fromisoformat(source.config["query_log"]["last_aggregated_at"])
    # Window 1 succeeded and was persisted; window 2 failed and the loop stopped there --
    # watermark must reflect exactly window 1's end, not silently jump past the failed window.
    assert persisted == now - timedelta(hours=6)
