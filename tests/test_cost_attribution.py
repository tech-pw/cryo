"""Tests for cryo.telemetry.cost_attribution -- query-driven cost attribution/chargeback.

_upsert_rollup uses a Postgres-only on_conflict_do_update (the test session is sqlite), so
windowing/watermark/decode tests below monkeypatch it out and assert on what it was called
with, same pattern as tests/test_iceberg_query_log_ingest.py uses for the sibling
iceberg_source.aggregate_from_iceberg_query_log.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from cryo.cost.model import TrinoScanCostPrice
from cryo.db.models import QueryCostAttributionDaily, Source, Table
from cryo.telemetry import cost_attribution
from cryo.telemetry.cost_attribution import (
    _normalize_client,
    _sum_physical_bytes,
    aggregate_query_cost_attribution,
    apply_scan_cost_prices,
    bytes_scanned_by_day,
)


def _seed_source(session, *, schema_name: str = "s", table_name: str = "t") -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="hive", config={})
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
    session.commit()
    return source


class TestNormalizeClient:
    def test_strips_dashboard_uuid_suffix(self) -> None:
        raw = "Metabase v0.60.0 [87b39d26-f81a-459a-b06b-836f6b3f68aa]"
        assert _normalize_client(raw) == "Metabase v0.60.0"

    def test_leaves_client_without_bracket_suffix_unchanged(self) -> None:
        assert _normalize_client("dbt-trino-1.9.2") == "dbt-trino-1.9.2"

    def test_none_becomes_unknown(self) -> None:
        assert _normalize_client(None) == "unknown"

    def test_empty_string_becomes_unknown(self) -> None:
        assert _normalize_client("") == "unknown"


class TestSumPhysicalBytes:
    def test_sums_across_multiple_input_tables(self) -> None:
        inputs_json = json.dumps(
            [
                {"schema": "a", "table": "t1", "physicalInputBytes": 1000},
                {"schema": "b", "table": "t2", "physicalInputBytes": 2500},
            ]
        )
        assert _sum_physical_bytes(inputs_json) == 3500

    def test_missing_bytes_field_contributes_zero(self) -> None:
        inputs_json = json.dumps([{"schema": "a", "table": "t1"}])
        assert _sum_physical_bytes(inputs_json) == 0

    def test_none_returns_zero(self) -> None:
        assert _sum_physical_bytes(None) == 0

    def test_unparseable_json_returns_zero(self) -> None:
        assert _sum_physical_bytes("not-json{{{") == 0

    def test_non_list_top_level_returns_zero(self) -> None:
        assert _sum_physical_bytes(json.dumps({"not": "a list"})) == 0

    def test_non_numeric_bytes_value_is_skipped_not_fatal(self) -> None:
        inputs_json = json.dumps(
            [
                {"physicalInputBytes": "not-a-number"},
                {"physicalInputBytes": 500},
            ]
        )
        assert _sum_physical_bytes(inputs_json) == 500


def _patch_source_catalog(monkeypatch, catalog: object) -> None:
    monkeypatch.setattr("cryo.sources.factory.source_catalog_for", lambda source: catalog)


class _FakeColumn:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def to_pylist(self) -> list[object]:
        return self._values


class _FakeBatch:
    def __init__(self, rows: dict[str, list[object]]) -> None:
        self._rows = rows

    @property
    def column_names(self) -> list[str]:
        return list(self._rows.keys())

    def column(self, name: str) -> _FakeColumn:
        return _FakeColumn(self._rows[name])


class _FakeScan:
    def __init__(self, batches: list[_FakeBatch]) -> None:
        self._batches = batches

    def to_arrow_batch_reader(self):
        return iter(self._batches)


class _FakeIcebergTable:
    def __init__(self, batches: list[_FakeBatch]) -> None:
        self._batches = batches

    def scan(self, *, row_filter, selected_fields):
        return _FakeScan(self._batches)


class _FakeCatalog:
    def __init__(self, table: _FakeIcebergTable) -> None:
        self._table = table

    def load_table(self, namespace: str, table_name: str) -> _FakeIcebergTable:
        return self._table


def _row_batch(rows: list[dict[str, object]]) -> _FakeBatch:
    columns: dict[str, list[object]] = {}
    for row in rows:
        for k, v in row.items():
            columns.setdefault(k, []).append(v)
    return _FakeBatch(columns)


class TestAggregateQueryCostAttribution:
    def test_no_query_log_config_returns_empty_result(self, session, monkeypatch) -> None:
        source = _seed_source(session)
        result = aggregate_query_cost_attribution(session, source)
        assert result.windows_processed == 0
        assert result.rows_upserted == 0

    def test_rolls_up_bytes_by_day_client_schema(self, session, monkeypatch) -> None:
        source = _seed_source(session)
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        source.config = {
            "query_log": {"mode": "iceberg_table", "table": "ns.t", "executed_at_column": "d"},
            # Exactly one 6h window between watermark and now -- the fake catalog below
            # doesn't filter by row_filter (unlike real PyIceberg), so it would return the
            # same batch on every window if more than one window ran in this call.
            "cost_attribution": {"last_aggregated_at": (now - timedelta(hours=6)).isoformat()},
        }
        session.commit()

        query_time = now - timedelta(hours=1)
        batch = _row_batch(
            [
                {
                    "query": "select 1",
                    "d": query_time,
                    "source": "Metabase v0.60.0 [abc-123]",
                    "schema": "analytics",
                    "query_state": "FINISHED",
                    "inputs_json": json.dumps(
                        [{"schema": "analytics", "table": "t", "physicalInputBytes": 1000}]
                    ),
                },
                {
                    "query": "select 2",
                    "d": query_time,
                    "source": "Metabase v0.60.0 [abc-123]",
                    "schema": "analytics",
                    "query_state": "FAILED",
                    "inputs_json": json.dumps(
                        [{"schema": "analytics", "table": "t", "physicalInputBytes": 500}]
                    ),
                },
            ]
        )
        _patch_source_catalog(monkeypatch, _FakeCatalog(_FakeIcebergTable([batch])))

        captured: dict = {}

        def _fake_upsert(session, source_id, rollup):
            captured.update(rollup)
            return len(rollup)

        monkeypatch.setattr(cost_attribution, "_upsert_rollup", _fake_upsert)

        result = aggregate_query_cost_attribution(
            session, source, now=now, window_hours=6, max_windows_per_tick=4
        )

        assert result.queries_read == 2
        # No "user" column in this fixture's rows -- falls back to "unknown", same as an
        # unconfigured/disabled user_column would.
        key = (query_time.date(), "Metabase v0.60.0", "analytics", "unknown")
        assert key in captured
        query_count, failed_count, bytes_scanned = captured[key]
        assert query_count == 2
        assert failed_count == 1
        assert bytes_scanned == 1500

    def test_rolls_up_by_user_when_user_column_present(self, session, monkeypatch) -> None:
        source = _seed_source(session)
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        source.config = {
            "query_log": {"mode": "iceberg_table", "table": "ns.t", "executed_at_column": "d"},
            "cost_attribution": {"last_aggregated_at": (now - timedelta(hours=6)).isoformat()},
        }
        session.commit()

        query_time = now - timedelta(hours=1)
        batch = _row_batch(
            [
                {
                    "query": "select 1",
                    "d": query_time,
                    "source": "trino-python-client",
                    "schema": "analytics",
                    "query_state": "FINISHED",
                    "inputs_json": json.dumps(
                        [{"schema": "analytics", "table": "t", "physicalInputBytes": 1000}]
                    ),
                    "user": "alice",
                },
                {
                    "query": "select 2",
                    "d": query_time,
                    "source": "trino-python-client",
                    "schema": "analytics",
                    "query_state": "FINISHED",
                    "inputs_json": json.dumps(
                        [{"schema": "analytics", "table": "t", "physicalInputBytes": 2000}]
                    ),
                    "user": "bob",
                },
            ]
        )
        _patch_source_catalog(monkeypatch, _FakeCatalog(_FakeIcebergTable([batch])))

        captured: dict = {}

        def _fake_upsert(session, source_id, rollup):
            captured.update(rollup)
            return len(rollup)

        monkeypatch.setattr(cost_attribution, "_upsert_rollup", _fake_upsert)

        result = aggregate_query_cost_attribution(
            session, source, now=now, window_hours=6, max_windows_per_tick=4
        )

        assert result.queries_read == 2
        alice_key = (query_time.date(), "trino-python-client", "analytics", "alice")
        bob_key = (query_time.date(), "trino-python-client", "analytics", "bob")
        assert captured[alice_key] == [1, 0, 1000]
        assert captured[bob_key] == [1, 0, 2000]

    def test_user_column_disabled_falls_back_to_unknown(self, session, monkeypatch) -> None:
        """user_column explicitly disabled (null/false) must roll up under 'unknown' even when
        the source's own event-listener table does carry a 'user' column -- disabling means
        'don't read it', not 'read it but ignore the value'."""
        source = _seed_source(session)
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        source.config = {
            "query_log": {
                "mode": "iceberg_table",
                "table": "ns.t",
                "executed_at_column": "d",
                "user_column": False,
            },
            "cost_attribution": {"last_aggregated_at": (now - timedelta(hours=6)).isoformat()},
        }
        session.commit()

        query_time = now - timedelta(hours=1)
        batch = _row_batch(
            [
                {
                    "query": "select 1",
                    "d": query_time,
                    "source": "trino-python-client",
                    "schema": "analytics",
                    "query_state": "FINISHED",
                    "inputs_json": json.dumps(
                        [{"schema": "analytics", "table": "t", "physicalInputBytes": 1000}]
                    ),
                    "user": "alice",
                },
            ]
        )
        _patch_source_catalog(monkeypatch, _FakeCatalog(_FakeIcebergTable([batch])))

        captured: dict = {}

        def _fake_upsert(session, source_id, rollup):
            captured.update(rollup)
            return len(rollup)

        monkeypatch.setattr(cost_attribution, "_upsert_rollup", _fake_upsert)

        aggregate_query_cost_attribution(
            session, source, now=now, window_hours=6, max_windows_per_tick=4
        )

        key = (query_time.date(), "trino-python-client", "analytics", "unknown")
        assert key in captured

    def test_watermark_advances_and_persists(self, session, monkeypatch) -> None:
        source = _seed_source(session)
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        source.config = {
            "query_log": {
                "mode": "iceberg_table",
                "table": "ns.t",
                "executed_at_column": "d",
            },
            "cost_attribution": {"last_aggregated_at": (now - timedelta(hours=6)).isoformat()},
        }
        session.commit()

        _patch_source_catalog(monkeypatch, _FakeCatalog(_FakeIcebergTable([_row_batch([])])))
        monkeypatch.setattr(cost_attribution, "_upsert_rollup", lambda *a, **k: 0)

        aggregate_query_cost_attribution(
            session, source, now=now, window_hours=6, max_windows_per_tick=4
        )

        session.refresh(source)
        persisted = source.config["cost_attribution"]["last_aggregated_at"]
        assert datetime.fromisoformat(persisted) == now

    def test_resumes_from_its_own_watermark_not_query_logs_watermark(
        self, session, monkeypatch
    ) -> None:
        """cost_attribution tracks a separate watermark from iceberg_source's query_log
        aggregation, even though both read the same underlying table -- a failure in one
        must not stall the other."""
        source = _seed_source(session)
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        source.config = {
            "query_log": {
                "mode": "iceberg_table",
                "table": "ns.t",
                "executed_at_column": "d",
                "last_aggregated_at": (now - timedelta(hours=1)).isoformat(),
            },
            "cost_attribution": {
                "last_aggregated_at": (now - timedelta(hours=6)).isoformat(),
            },
        }
        session.commit()

        calls: list[tuple] = []

        class _RecordingTable(_FakeIcebergTable):
            def scan(self, *, row_filter, selected_fields):
                calls.append(row_filter)
                return _FakeScan([_row_batch([])])

        _patch_source_catalog(monkeypatch, _FakeCatalog(_RecordingTable([_row_batch([])])))
        monkeypatch.setattr(cost_attribution, "_upsert_rollup", lambda *a, **k: 0)

        aggregate_query_cost_attribution(
            session, source, now=now, window_hours=6, max_windows_per_tick=1
        )

        session.refresh(source)
        # query_log's own watermark must be untouched by this call.
        assert (
            source.config["query_log"]["last_aggregated_at"]
            == (now - timedelta(hours=1)).isoformat()
        )
        assert len(calls) == 1

    def test_read_failure_stops_without_advancing_watermark_for_that_window(
        self, session, monkeypatch
    ) -> None:
        source = _seed_source(session)
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        source.config = {
            "query_log": {
                "mode": "iceberg_table",
                "table": "ns.t",
                "executed_at_column": "d",
                "last_aggregated_at": (now - timedelta(hours=6)).isoformat(),
            }
        }
        session.commit()

        class _BrokenTable(_FakeIcebergTable):
            def scan(self, *, row_filter, selected_fields):
                raise RuntimeError("catalog unreachable")

        _patch_source_catalog(monkeypatch, _FakeCatalog(_BrokenTable([])))

        result = aggregate_query_cost_attribution(session, source, now=now)

        assert result.windows_processed == 0
        session.refresh(source)
        assert source.config.get("cost_attribution") is None


class TestBytesScannedByDay:
    def test_sums_across_clients_and_schemas_for_the_same_day(self, session) -> None:
        source = _seed_source(session)
        day = date(2026, 8, 14)
        session.add_all(
            [
                QueryCostAttributionDaily(
                    source_id=source.id,
                    day=day,
                    client="Metabase",
                    schema_name="revenue",
                    query_count=10,
                    failed_query_count=0,
                    physical_bytes_scanned=100,
                ),
                QueryCostAttributionDaily(
                    source_id=source.id,
                    day=day,
                    client="dbt",
                    schema_name="admin",
                    query_count=5,
                    failed_query_count=0,
                    physical_bytes_scanned=50,
                ),
            ]
        )
        session.commit()

        result = bytes_scanned_by_day(session, since=day)
        assert result == {day: 150}

    def test_excludes_days_before_since(self, session) -> None:
        source = _seed_source(session)
        old_day = date(2026, 8, 1)
        recent_day = date(2026, 8, 14)
        session.add_all(
            [
                QueryCostAttributionDaily(
                    source_id=source.id,
                    day=old_day,
                    client="Metabase",
                    schema_name="revenue",
                    query_count=1,
                    failed_query_count=0,
                    physical_bytes_scanned=999,
                ),
                QueryCostAttributionDaily(
                    source_id=source.id,
                    day=recent_day,
                    client="Metabase",
                    schema_name="revenue",
                    query_count=1,
                    failed_query_count=0,
                    physical_bytes_scanned=100,
                ),
            ]
        )
        session.commit()

        result = bytes_scanned_by_day(session, since=recent_day)
        assert result == {recent_day: 100}


class TestApplyScanCostPrices:
    def test_writes_cost_proportional_to_each_rows_byte_share(self, session) -> None:
        source = _seed_source(session)
        day = date(2026, 8, 14)
        # Metabase: 300 of the day's 400 total bytes (75%); dbt: 100 (25%).
        session.add_all(
            [
                QueryCostAttributionDaily(
                    source_id=source.id,
                    day=day,
                    client="Metabase",
                    schema_name="revenue",
                    query_count=10,
                    failed_query_count=0,
                    physical_bytes_scanned=300,
                ),
                QueryCostAttributionDaily(
                    source_id=source.id,
                    day=day,
                    client="dbt",
                    schema_name="admin",
                    query_count=5,
                    failed_query_count=0,
                    physical_bytes_scanned=100,
                ),
            ]
        )
        session.commit()

        gib = 1_073_741_824
        price = TrinoScanCostPrice(
            day=day, price_usd_per_gib=10.0, namespace_cost_usd=4.0 * gib, bytes_scanned=400
        )
        updated = apply_scan_cost_prices(session, [price])
        session.commit()

        assert updated == 2
        rows = {
            row.client: row.estimated_scan_cost_usd
            for row in session.query(QueryCostAttributionDaily).all()
        }
        assert rows["Metabase"] == round((300 / gib) * 10.0, 6)
        assert rows["dbt"] == round((100 / gib) * 10.0, 6)
        # Metabase's larger byte share must get proportionally more cost attributed to it.
        assert rows["Metabase"] > rows["dbt"]

    def test_leaves_unpriced_days_untouched(self, session) -> None:
        """A day absent from `prices` (see derive_trino_scan_cost_prices docstring) must keep
        whatever estimated_scan_cost_usd it already had -- an old real price beats erasing it
        just because today's fetch didn't cover that day."""
        source = _seed_source(session)
        day = date(2026, 8, 14)
        row = QueryCostAttributionDaily(
            source_id=source.id,
            day=day,
            client="Metabase",
            schema_name="revenue",
            query_count=10,
            failed_query_count=0,
            physical_bytes_scanned=300,
            estimated_scan_cost_usd=1.23,
        )
        session.add(row)
        session.commit()

        updated = apply_scan_cost_prices(session, [])
        session.commit()

        assert updated == 0
        session.refresh(row)
        assert row.estimated_scan_cost_usd == 1.23
