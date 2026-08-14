"""Tests for cryo.optimizer.layout_analyzer.fetch_file_stats and analyze_table_layout's
self-fetch behavior.

Regression, found 2026-08-14: analyze_table_layout's file_stats parameter always defaulted to
None, and none of its 3 real callers (generate_layout_recommendation, advanced_routes.py,
property_recommendations.py) ever passed it -- so current_prune_efficiency was always computed
from an unset 0.0 baseline instead of real Iceberg file bounds, making every LayoutRecommendation's
estimated_prune_improvement identical (a function of the hardcoded target efficiency alone)
regardless of the table's actual layout. 1,686 pending recommendations all showed ~0.56%
improvement -- this is the real fix.

fetch_file_stats went through two implementations the same day: an initial version reading
table.inspect.files() (simple, but confirmed live to take 23s on a 93,699-file real table
because it materializes the whole Arrow result before any sampling), replaced with the current
one using cryo.scanner.analyze.iter_data_files (streaming manifest entries, ~5s for the same
sample size on the same table). Tests below exercise the current (manifest-based) shape.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pyiceberg.conversions import to_bytes
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField, StringType

from cryo.db.models import Source, Table, TableStats
from cryo.optimizer.layout_analyzer import analyze_table_layout, fetch_file_stats

_SCHEMA = Schema(
    NestedField(field_id=1, name="event_date", field_type=StringType(), required=False),
    NestedField(field_id=2, name="amount", field_type=LongType(), required=False),
)


class _FakeDataFile:
    def __init__(self, lower_bounds: dict[int, bytes], upper_bounds: dict[int, bytes]) -> None:
        self.lower_bounds = lower_bounds
        self.upper_bounds = upper_bounds


class _FakeEntry:
    def __init__(self, data_file: _FakeDataFile, status: str = "ADDED") -> None:
        self.data_file = data_file
        self.status = status


class _FakeManifest:
    def __init__(self, entries: list[_FakeEntry]) -> None:
        self._entries = entries

    def fetch_manifest_entry(
        self, io: object = None, discard_deleted: bool = True
    ) -> list[_FakeEntry]:
        if discard_deleted:
            return [e for e in self._entries if e.status != "DELETED"]
        return list(self._entries)


class _FakeSnapshot:
    def __init__(self, manifest_list: list[_FakeManifest]) -> None:
        self._manifest_list = manifest_list

    def manifests(self, io: object) -> list[_FakeManifest]:
        return self._manifest_list


class _FakeIcebergTable:
    def __init__(self, manifests: list[_FakeManifest], schema: Schema = _SCHEMA) -> None:
        self._manifests = manifests
        self._schema = schema
        self.io = None

    def current_snapshot(self) -> _FakeSnapshot:
        return _FakeSnapshot(self._manifests)

    def schema(self) -> Schema:
        return self._schema


class _FakeCatalog:
    def __init__(self, table: _FakeIcebergTable) -> None:
        self._table = table

    def load_table(self, namespace: str, table_name: str) -> _FakeIcebergTable:
        return self._table


def _entry_for(
    event_date_range: tuple[str, str] | None = None,
    amount_range: tuple[int, int] | None = None,
) -> _FakeEntry:
    lower: dict[int, bytes] = {}
    upper: dict[int, bytes] = {}
    if event_date_range is not None:
        lower[1] = to_bytes(StringType(), event_date_range[0])
        upper[1] = to_bytes(StringType(), event_date_range[1])
    if amount_range is not None:
        lower[2] = to_bytes(LongType(), amount_range[0])
        upper[2] = to_bytes(LongType(), amount_range[1])
    return _FakeEntry(_FakeDataFile(lower, upper))


def _seed_table(session, *, schema_name: str = "s", table_name: str = "t") -> Table:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="hive", config={})
    session.add(source)
    session.flush()
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name=schema_name,
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.commit()
    return table


class TestFetchFileStats:
    def test_decodes_lower_and_upper_bounds_by_column_name(self, session, monkeypatch) -> None:
        table = _seed_table(session)
        manifest = _FakeManifest(
            [
                _entry_for(event_date_range=("2026-01-01", "2026-01-01")),
                _entry_for(event_date_range=("2026-01-02", "2026-01-02")),
            ]
        )
        monkeypatch.setattr(
            "cryo.sources.factory.source_catalog_for",
            lambda source: _FakeCatalog(_FakeIcebergTable([manifest])),
        )

        result = fetch_file_stats(session, table)

        assert result == [
            {
                "lower_bounds": {"event_date": "2026-01-01"},
                "upper_bounds": {"event_date": "2026-01-01"},
            },
            {
                "lower_bounds": {"event_date": "2026-01-02"},
                "upper_bounds": {"event_date": "2026-01-02"},
            },
        ]

    def test_multiple_columns_decoded_independently(self, session, monkeypatch) -> None:
        table = _seed_table(session)
        manifest = _FakeManifest(
            [_entry_for(event_date_range=("2026-01-01", "2026-01-02"), amount_range=(10, 99))]
        )
        monkeypatch.setattr(
            "cryo.sources.factory.source_catalog_for",
            lambda source: _FakeCatalog(_FakeIcebergTable([manifest])),
        )

        result = fetch_file_stats(session, table)

        assert result == [
            {
                "lower_bounds": {"event_date": "2026-01-01", "amount": 10},
                "upper_bounds": {"event_date": "2026-01-02", "amount": 99},
            }
        ]

    def test_deleted_entries_are_excluded(self, session, monkeypatch) -> None:
        table = _seed_table(session)
        alive = _entry_for(event_date_range=("2026-01-01", "2026-01-01"))
        deleted = _entry_for(event_date_range=("2099-01-01", "2099-01-01"))
        deleted.status = "DELETED"
        manifest = _FakeManifest([alive, deleted])
        monkeypatch.setattr(
            "cryo.sources.factory.source_catalog_for",
            lambda source: _FakeCatalog(_FakeIcebergTable([manifest])),
        )

        result = fetch_file_stats(session, table)

        assert len(result) == 1
        assert result[0]["lower_bounds"]["event_date"] == "2026-01-01"

    def test_files_with_no_bounds_are_skipped(self, session, monkeypatch) -> None:
        table = _seed_table(session)
        manifest = _FakeManifest([_entry_for()])  # no ranges set at all
        monkeypatch.setattr(
            "cryo.sources.factory.source_catalog_for",
            lambda source: _FakeCatalog(_FakeIcebergTable([manifest])),
        )

        assert fetch_file_stats(session, table) == []

    def test_no_manifests_returns_empty_list_not_none(self, session, monkeypatch) -> None:
        table = _seed_table(session)
        monkeypatch.setattr(
            "cryo.sources.factory.source_catalog_for",
            lambda source: _FakeCatalog(_FakeIcebergTable([])),
        )

        assert fetch_file_stats(session, table) == []

    def test_catalog_failure_returns_none(self, session, monkeypatch) -> None:
        table = _seed_table(session)

        class _BrokenCatalog:
            def load_table(self, namespace: str, table_name: str) -> None:
                raise RuntimeError("catalog unreachable")

        monkeypatch.setattr(
            "cryo.sources.factory.source_catalog_for", lambda source: _BrokenCatalog()
        )

        assert fetch_file_stats(session, table) is None

    def test_missing_source_returns_none(self, session) -> None:
        table = Table(
            id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            schema_name="s",
            table_name="t",
            is_active=True,
        )
        session.add(table)
        session.commit()

        assert fetch_file_stats(session, table) is None

    def test_stops_at_sample_size_even_with_more_files_available(
        self, session, monkeypatch
    ) -> None:
        table = _seed_table(session)
        entries = [_entry_for(event_date_range=(f"d{i}", f"d{i}")) for i in range(500)]
        manifest = _FakeManifest(entries)
        monkeypatch.setattr(
            "cryo.sources.factory.source_catalog_for",
            lambda source: _FakeCatalog(_FakeIcebergTable([manifest])),
        )

        result = fetch_file_stats(session, table, sample_size=10)

        assert result is not None
        assert len(result) == 10

    def test_unknown_field_id_is_skipped_not_fatal(self, session, monkeypatch) -> None:
        """A bound referencing a field ID not present in the current schema (e.g. a dropped
        column) must not crash the whole fetch -- just that one bound is dropped."""
        table = _seed_table(session)
        bad_entry = _FakeEntry(
            _FakeDataFile(
                lower_bounds={999: b"\x00", 1: to_bytes(StringType(), "2026-01-01")},
                upper_bounds={999: b"\x00", 1: to_bytes(StringType(), "2026-01-01")},
            )
        )
        manifest = _FakeManifest([bad_entry])
        monkeypatch.setattr(
            "cryo.sources.factory.source_catalog_for",
            lambda source: _FakeCatalog(_FakeIcebergTable([manifest])),
        )

        result = fetch_file_stats(session, table)

        assert result == [
            {
                "lower_bounds": {"event_date": "2026-01-01"},
                "upper_bounds": {"event_date": "2026-01-01"},
            }
        ]


class TestAnalyzeTableLayoutSelfFetch:
    def _seed_table_with_stats(self, session) -> Table:
        table = _seed_table(session)
        session.add(
            TableStats(
                table_id=table.id,
                scan_at=datetime.now(UTC),
                needs_compaction=False,
                compaction_priority="LOW",
                grade="C",
                overall_score=65,
                total_files=2,
                small_files_count=0,
                total_size_gb=1.0,
                query_count_30d=0,
            )
        )
        session.commit()
        return table

    def test_omitting_file_stats_triggers_a_real_fetch(self, session, monkeypatch) -> None:
        table = self._seed_table_with_stats(session)
        manifest = _FakeManifest(
            [
                _entry_for(event_date_range=("2026-01-01", "2026-01-02")),
                _entry_for(event_date_range=("2026-02-01", "2026-02-02")),
            ]
        )
        monkeypatch.setattr(
            "cryo.sources.factory.source_catalog_for",
            lambda source: _FakeCatalog(_FakeIcebergTable([manifest])),
        )

        analysis = analyze_table_layout(session, table.id)

        # Non-overlapping ranges across files -- current_prune_efficiency should be computed
        # from real data (high, since the two files' event_date ranges don't overlap at all),
        # not stuck at the old hardcoded 0.0 default.
        assert analysis.current_prune_efficiency > 0.0
        assert len(analysis.column_stats) == 1
        assert analysis.column_stats[0].column_name == "event_date"

    def test_explicit_empty_file_stats_skips_the_fetch(self, session, monkeypatch) -> None:
        table = self._seed_table_with_stats(session)

        def _should_not_be_called(source: object) -> None:
            raise AssertionError("source_catalog_for should not be called when file_stats=[]")

        monkeypatch.setattr("cryo.sources.factory.source_catalog_for", _should_not_be_called)

        analysis = analyze_table_layout(session, table.id, file_stats=[])

        assert analysis.current_prune_efficiency == 0.0
        assert analysis.column_stats == []

    def test_explicit_prefetched_file_stats_skips_the_fetch(self, session, monkeypatch) -> None:
        table = self._seed_table_with_stats(session)

        def _should_not_be_called(source: object) -> None:
            raise AssertionError("source_catalog_for should not be called with real file_stats")

        monkeypatch.setattr("cryo.sources.factory.source_catalog_for", _should_not_be_called)

        analysis = analyze_table_layout(
            session,
            table.id,
            file_stats=[{"lower_bounds": {"a": 1}, "upper_bounds": {"a": 10}}],
        )

        assert len(analysis.column_stats) == 1

    def test_fetch_failure_degrades_to_no_file_analysis_not_a_crash(
        self, session, monkeypatch
    ) -> None:
        table = self._seed_table_with_stats(session)

        class _BrokenCatalog:
            def load_table(self, namespace: str, table_name: str) -> None:
                raise RuntimeError("catalog unreachable")

        monkeypatch.setattr(
            "cryo.sources.factory.source_catalog_for", lambda source: _BrokenCatalog()
        )

        analysis = analyze_table_layout(session, table.id)

        assert analysis.current_prune_efficiency == 0.0
        assert analysis.column_stats == []
