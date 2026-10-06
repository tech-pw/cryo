"""Unit tests for the new query-log tiered pipeline.

Covers the parts of the pipeline that don't need a real Trino cluster, a real Redis,
or a real Iceberg catalog:

- ``QueryLogConfig`` parsing from a ``Destination.query_log_config`` JSONB block.
- Cadence derivation from retention (short retention => faster pulls, wider overlap).
- Sampling is deterministic by query_id so a crash + re-fetch selects the same subset.
- The Iceberg writer builds the expected schema and skips malformed rows.
- Multi-coordinator endpoint expansion yields every endpoint per tick (no round-robin).
- The curator's curator-level curator-level result surfaces the right counters in the
  "no source linked" case (Iceberg still archives, curated spill skipped).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from cryo.telemetry.iceberg_writer import (
    QueryLogIcebergWriter,
    build_query_log_schema,
    derive_pull_cadence,
    enrich_row_with_query_stats,
    parse_query_log_config,
    retention_for_destination,
    rows_from_trino_queries,
    sample_query,
)
from cryo.telemetry.query_buffer import (
    DEFAULT_MAXLEN,
    _stream_key,
)

# ---------------------------------------------------------------------------
# parse_query_log_config + cadence derivation
# ---------------------------------------------------------------------------


def test_parse_returns_none_for_missing_config() -> None:
    assert parse_query_log_config(None) is None


def test_parse_returns_none_for_disabled_config() -> None:
    assert parse_query_log_config({"enabled": False}) is None


def test_parse_defaults_to_trino_system_runtime() -> None:
    cfg = parse_query_log_config({"enabled": True})
    assert cfg is not None
    assert cfg.mode == "trino_system_runtime"
    assert cfg.namespace == "cryo_internal"
    assert cfg.table_name == "queries"
    assert cfg.pull_interval_seconds == 300  # default cadence
    # Overlap defaults to 5x pull interval, floored at 5 min
    assert cfg.watermark_overlap_seconds == 1500  # 5 * 300
    assert cfg.telemetry_coordinators == ()


def test_parse_clamps_out_of_range_sample_rate() -> None:
    cfg = parse_query_log_config({"enabled": True, "sample_rate": 1.5})
    assert cfg is not None
    assert cfg.sample_rate == 1.0
    cfg = parse_query_log_config({"enabled": True, "sample_rate": -0.1})
    assert cfg is not None
    assert cfg.sample_rate == 0.0


def test_parse_coerces_telemetry_coordinators_from_string() -> None:
    cfg = parse_query_log_config({"enabled": True, "telemetry_coordinators": "http://trino-1:8080"})
    assert cfg is not None
    assert cfg.telemetry_coordinators == ("http://trino-1:8080",)


def test_parse_coerces_telemetry_coordinators_from_list() -> None:
    cfg = parse_query_log_config(
        {
            "enabled": True,
            "telemetry_coordinators": [
                "http://trino-1:8080",
                "http://trino-2:8080",
                "",
            ],
        }
    )
    assert cfg is not None
    # Empty strings are dropped
    assert cfg.telemetry_coordinators == (
        "http://trino-1:8080",
        "http://trino-2:8080",
    )


def test_parse_floor_enforces_overlap_above_pull_interval() -> None:
    """A curator tick must always pull more than the interval; floor enforced regardless
    of user input -- a too-small overlap would silently lose data on a missed tick.
    """
    cfg = parse_query_log_config(
        {
            "enabled": True,
            "pull_interval_seconds": 60,
            "watermark_overlap_seconds": 10,  # attempt to undercut
        }
    )
    assert cfg is not None
    # 5-min floor is the absolute minimum
    assert cfg.watermark_overlap_seconds >= 300
    # And overlap is at least 5x the pull interval
    assert cfg.watermark_overlap_seconds >= 5 * cfg.pull_interval_seconds


def test_cadence_picks_60s_pull_for_short_retention() -> None:
    """retention <= 1 day: pull every minute, overlap at 5 min (5x)."""
    pull, overlap = derive_pull_cadence(1)
    assert pull == 60
    assert overlap == 300


def test_cadence_picks_300s_pull_for_long_retention() -> None:
    """retention > 1 day: pull every 5 min, overlap at 25 min (5x)."""
    pull, overlap = derive_pull_cadence(30)
    assert pull == 300
    assert overlap == 1500  # 5 * pull


def test_cadence_uses_safe_defaults_for_unset_retention() -> None:
    """No retention (read_through mode): cadence doesn't matter, but overlap is sane."""
    pull, overlap = derive_pull_cadence(None)
    assert pull == 300
    assert overlap == 300  # 5-min floor


# ---------------------------------------------------------------------------
# Sampling determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sample_rate,expected_at_least_one_in",
    [
        (1.0, True),
        (0.5, True),
        (0.0, False),
    ],
)
def test_sample_query_is_deterministic_for_same_id(sample_rate, expected_at_least_one_in) -> None:
    """Same query_id under same sample rate MUST always return the same verdict.

    This is the property that lets a curator crash + re-fetch pick exactly the same
    subset, so aggregate counts don't drift across restarts.
    """
    qid = "abc-123-stuck-on-this-query"
    # 100 calls in a row should all agree
    verdicts = [sample_query(qid, sample_rate) for _ in range(100)]
    assert len(set(verdicts)) == 1
    if sample_rate > 0.0:
        # Should sample in at 50% rate ~50% of the time across 1000 different ids
        sampled = sum(sample_query(f"q{i}", 0.5) for i in range(1000))
        # Allow generous bounds -- 1000 samples, p=0.5, std dev ~15.8, range 400-600 covers >99.7%
        assert 350 <= sampled <= 650


# ---------------------------------------------------------------------------
# Iceberg writer schema
# ---------------------------------------------------------------------------


def test_query_log_schema_has_expected_fields() -> None:
    """Schema is frozen: same column names + types across all deployments.

    Reader code in :func:`cryo.telemetry.iceberg_source._read_query_texts` and any future
    Trino/Spark views on the table rely on the exact names -- a rename is an Iceberg
    schema evolution but never silent.
    """
    schema = build_query_log_schema()
    field_names = {f.name for f in schema.fields}
    assert {"query_id", "source_id", "engine", "executed_at", "query_text"}.issubset(field_names)
    # query_id + executed_at are the only required fields
    required = {f.name for f in schema.fields if f.required}
    assert required == {"query_id", "source_id", "engine", "executed_at"}


# ---------------------------------------------------------------------------
# Row -> PyArrow conversion ()
#
# PyIceberg's Table.append() requires a pyarrow.Table -- passing a raw list of dicts fails
# with "Expected PyArrow table, got: [...]". This was the fourth bug in the  chain,
# only surfaced once auth + the SQL column mismatch + the catalog-wrapper mismatch were all
# fixed and a real batch of rows finally reached this call.
# ---------------------------------------------------------------------------


def test_rows_to_arrow_table_preserves_values_and_field_ids() -> None:
    from cryo.telemetry.iceberg_writer import _rows_to_arrow_table

    rows = [
        {
            "query_id": "q1",
            "source_id": "s1",
            "engine": "trino",
            "executed_at": datetime(2026, 8, 18, 6, 0, 0, tzinfo=UTC),
            "query_text": "SELECT 1",
            "user_name": "bob",
            "duration_ms": 100,
        }
    ]
    table = _rows_to_arrow_table(rows)
    assert table.num_rows == 1
    assert table.column("query_id").to_pylist() == ["q1"]
    assert table.column("user_name").to_pylist() == ["bob"]
    # Field IDs must match the Iceberg schema's -- PyIceberg uses these to map Arrow columns
    # to Iceberg fields on write, not column position or name alone.
    query_id_field = table.schema.field("query_id")
    assert query_id_field.metadata[b"PARQUET:field_id"] == b"1"


def test_rows_to_arrow_table_handles_missing_optional_fields_as_null() -> None:
    """Optional columns (everything except query_id/source_id/engine/executed_at) absent
    from a row -- e.g. a Trino version missing bytes_read-equivalent columns -- must convert
    to null, not raise.
    """
    from cryo.telemetry.iceberg_writer import _rows_to_arrow_table

    rows = [
        {
            "query_id": "q1",
            "source_id": "s1",
            "engine": "trino",
            "executed_at": datetime(2026, 8, 18, 6, 0, 0, tzinfo=UTC),
        }
    ]
    table = _rows_to_arrow_table(rows)
    assert table.column("bytes_read").to_pylist() == [None]
    assert table.column("query_text").to_pylist() == [None]


def test_rows_to_arrow_table_handles_multiple_rows() -> None:
    from cryo.telemetry.iceberg_writer import _rows_to_arrow_table

    rows = [
        {
            "query_id": f"q{i}",
            "source_id": "s1",
            "engine": "trino",
            "executed_at": datetime(2026, 8, 18, 6, 0, 0, tzinfo=UTC),
        }
        for i in range(3)
    ]
    table = _rows_to_arrow_table(rows)
    assert table.num_rows == 3
    assert table.column("query_id").to_pylist() == ["q0", "q1", "q2"]


def test_writer_skips_rows_with_no_query_id() -> None:
    """A row with no query_id is uncategorizable; the curator can't dedupe it. Drop
    rather than fail the whole batch.
    """
    fake_catalog = _FakeCatalog()
    writer = QueryLogIcebergWriter(
        catalog=fake_catalog, namespace="cryo_internal", table_name="queries"
    )
    result = writer.append(
        [
            {"query_id": "q1", "source_id": "s1", "executed_at": datetime.now(UTC)},
            {"source_id": "s1", "executed_at": datetime.now(UTC)},  # missing query_id
            {"query_id": "q2", "source_id": "s1", "executed_at": datetime.now(UTC)},
        ]
    )
    assert result.rows_appended == 2
    assert result.error is None


def test_writer_skips_unknown_columns_defensively() -> None:
    """Forward-compatible: extra columns in the batch are dropped, not rejected."""
    fake_catalog = _FakeCatalog()
    writer = QueryLogIcebergWriter(
        catalog=fake_catalog, namespace="cryo_internal", table_name="queries"
    )
    result = writer.append(
        [
            {
                "query_id": "q1",
                "source_id": "s1",
                "executed_at": datetime.now(UTC),
                "future_field": "ignored",
            }
        ]
    )
    assert result.rows_appended == 1
    assert fake_catalog.last_appended[0]["query_id"] == "q1"
    assert "future_field" not in fake_catalog.last_appended[0]


# ---------------------------------------------------------------------------
# Rows from Trino
# ---------------------------------------------------------------------------


def test_rows_from_trino_queries_skips_no_query_id() -> None:
    rows = rows_from_trino_queries(
        source_id="s1",
        queries=[
            {"query_id": "q1", "query": "SELECT 1"},
            {"query": "SELECT 2"},  # missing query_id
        ],
    )
    assert len(rows) == 1
    assert rows[0]["query_id"] == "q1"


def test_rows_from_trino_queries_truncates_huge_text() -> None:
    """10k char cap is the existing legacy limit; preserve it for consistency."""
    huge = "SELECT '" + ("x" * 50_000) + "'"
    rows = rows_from_trino_queries(
        source_id="s1",
        queries=[{"query_id": "q1", "query": huge}],
    )
    assert rows[0]["query_text"] is not None
    assert len(rows[0]["query_text"]) == 10_000


# ---------------------------------------------------------------------------
# Per-query stats enrichment (bytes_read/rows_read/files_read)
#
# system.runtime.queries has no scan-byte columns on this Trino deployment (confirmed
# live 2026-09-28 via DESCRIBE -- 15 columns, none of them stats), so rows_from_trino_queries
# always leaves bytes_read/rows_read/files_read as None. enrich_row_with_query_stats fills
# them from the real per-query REST API (/v1/query/{id}) response, whose shapes below are
# copied from an actual live response, not invented.
# ---------------------------------------------------------------------------


def test_enrich_fills_bytes_rows_files_from_real_query_detail_shape() -> None:
    row = {"query_id": "q1", "bytes_read": None, "rows_read": None, "files_read": None}
    # Real response shape confirmed live against coordinator-1.
    detail = {
        "queryStats": {
            "physicalInputDataSize": "4536702B",
            "physicalInputPositions": 5568,
        },
        "inputs": [
            {"catalogName": "sample", "schema": "analytics", "table": "silver_dbt_orders$files"},
            {"catalogName": "sample", "schema": "analytics", "table": "silver_dbt_users$files"},
        ],
    }
    enriched = enrich_row_with_query_stats(row, detail)
    assert enriched["bytes_read"] == 4536702
    assert enriched["rows_read"] == 5568
    assert enriched["files_read"] == 2
    assert enriched["_trino_inputs"][0]["table"] == "silver_dbt_orders$files"
    # Original row is untouched.
    assert row["bytes_read"] is None


@pytest.mark.parametrize(
    ("raw", "expected_bytes"),
    [
        ("4536702B", 4536702),
        ("1.5MB", int(1.5 * 1024**2)),
        ("2GB", 2 * 1024**3),
        ("0B", 0),
        (None, None),
        ("", None),
        ("garbage", None),
    ],
)
def test_enrich_parses_trino_data_size_units(raw: str | None, expected_bytes: int | None) -> None:
    detail = {"queryStats": {"physicalInputDataSize": raw}}
    enriched = enrich_row_with_query_stats({"query_id": "q1"}, detail)
    assert enriched.get("bytes_read") == expected_bytes


def test_enrich_leaves_row_unset_when_query_stats_missing() -> None:
    """A query_detail with no queryStats key (a malformed or partial response) must not
    raise -- the row keeps whatever it already had."""
    row = {"query_id": "q1", "bytes_read": None}
    enriched = enrich_row_with_query_stats(row, {})
    assert enriched["bytes_read"] is None


def test_enrich_leaves_files_read_none_when_inputs_missing() -> None:
    row = {"query_id": "q1", "files_read": None}
    enriched = enrich_row_with_query_stats(row, {"queryStats": {}})
    assert enriched["files_read"] is None


# ---------------------------------------------------------------------------
# Retention cutoff
# ---------------------------------------------------------------------------


def _make_destination_with_config(config: dict | None, retention: int | None) -> Any:
    """Tiny stand-in for a Destination ORM row -- just the fields the retention helper reads."""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=uuid4(),
        name="d",
        query_log_config=config,
        query_log_retention_days=retention,
    )


def test_retention_cutoff_uses_destination_value() -> None:
    dest = _make_destination_with_config({"enabled": True}, retention=14)
    cutoff = retention_for_destination(dest)
    assert cutoff is not None
    delta = datetime.now(UTC) - cutoff
    # Within a couple seconds of 14 days
    assert 13 < delta.days < 15


def test_retention_cutoff_is_none_for_read_through() -> None:
    """read_through mode points at the user's table; they own retention, not us."""
    dest = _make_destination_with_config({"enabled": True, "mode": "read_through"}, retention=14)
    assert retention_for_destination(dest) is None


def test_retention_cutoff_is_none_for_disabled_destination() -> None:
    dest = _make_destination_with_config(None, retention=14)
    assert retention_for_destination(dest) is None


# ---------------------------------------------------------------------------
# Stream-key naming
# ---------------------------------------------------------------------------


def test_stream_key_uses_source_id() -> None:
    assert _stream_key("abc-123") == "cryo:queries:source:abc-123"
    sid = uuid4()
    assert _stream_key(sid) == f"cryo:queries:source:{sid}"


# ---------------------------------------------------------------------------
# Catalog unwrapping (): writer_for_destination must hand the writer a raw
# PyIceberg catalog, not the read-oriented SourceCatalog wrapper (HiveSourceCatalog etc)
# that source_catalog_for() returns for every other caller. The wrapper doesn't expose
# create_table_if_not_exists/create_namespace_if_not_exists, so passing it through
# unmodified crashed every append with AttributeError once the fetch bug (above) was fixed.
# ---------------------------------------------------------------------------


def test_unwrap_pyiceberg_catalog_reaches_past_source_catalog_wrapper() -> None:
    from types import SimpleNamespace

    from cryo.telemetry.iceberg_writer import _unwrap_pyiceberg_catalog

    raw_catalog = _FakeCatalog()
    # Shape of a real PyIcebergSourceCatalog subclass: a wrapper object whose ._catalog
    # is the actual PyIceberg catalog with the write-side methods.
    wrapper = SimpleNamespace(_catalog=raw_catalog, list_tables=lambda: [])

    assert _unwrap_pyiceberg_catalog(wrapper) is raw_catalog


def test_unwrap_pyiceberg_catalog_falls_back_to_input_when_no_wrapper() -> None:
    """A caller that already hands over a raw catalog (no ._catalog attribute) should pass
    through unchanged rather than the unwrap silently losing it."""
    from cryo.telemetry.iceberg_writer import _unwrap_pyiceberg_catalog

    raw_catalog = _FakeCatalog()
    assert _unwrap_pyiceberg_catalog(raw_catalog) is raw_catalog


def test_catalog_for_unwraps_source_catalog_to_raw_pyiceberg_catalog() -> None:
    """End-to-end: _catalog_for(source, destination) must return something with
    create_table_if_not_exists -- the actual method QueryLogIcebergWriter._ensure_table
    calls -- not the SourceCatalog wrapper's narrower list_tables/load_table-only API.
    """
    from types import SimpleNamespace
    from unittest.mock import patch

    from cryo.telemetry.iceberg_writer import _catalog_for

    raw_catalog = _FakeCatalog()

    class _FakeSourceCatalogWrapper:
        def __init__(self, name: str, config: dict) -> None:  # noqa: ARG002
            self._catalog = raw_catalog

        def list_tables(self):  # pragma: no cover - not exercised here
            return []

    fake_source = SimpleNamespace(id=uuid4(), name="s", type="rest", config={})

    with patch(
        "cryo.sources.factory.source_catalog_for",
        return_value=_FakeSourceCatalogWrapper("s", {}),
    ):
        resolved = _catalog_for(fake_source, destination=SimpleNamespace(name="d", config={}))

    assert resolved is raw_catalog
    assert hasattr(resolved, "create_table_if_not_exists")


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeCatalog:
    """Minimal PyIceberg catalog double -- records appended rows for assertions.

    Tracks appended rows on the *table* object (since that's where the writer calls
    ``.append``) and mirrors them to a class-level list for ergonomic assertions.
    """

    def __init__(self) -> None:
        self.create_namespace_calls = 0
        self.create_table_calls: list[tuple[str, Any]] = []
        self.last_appended: list[dict] = []

    def create_namespace_if_not_exists(self, ns: str) -> None:
        self.create_namespace_calls += 1

    def create_table_if_not_exists(self, ident: str, *, schema, properties):  # type: ignore[no-untyped-def]

        self.create_table_calls.append((ident, schema))
        return _FakeTable(ident=ident, catalog=self)

    def load_table(self, ident: str):  # type: ignore[no-untyped-def]
        return _FakeTable(ident=ident, catalog=self)


class _FakeTable:
    """Stand-in for a PyIceberg Table. Records appended rows on the catalog for assertions."""

    def __init__(self, *, ident: str, catalog: _FakeCatalog) -> None:
        self.ident = ident
        self._catalog = catalog

    def append(self, arrow_table) -> None:  # type: ignore[no-untyped-def]
        # The real PyIceberg Table.append() takes a pyarrow.Table, not a list of dicts
        # () -- convert back to plain dicts so existing assertions on
        # last_appended stay simple.
        self._catalog.last_appended.extend(arrow_table.to_pylist())

    def expire_snapshots(self, older_than):  # type: ignore[no-untyped-def]
        from types import SimpleNamespace

        return SimpleNamespace(deleted_snapshots_count=0, deleted_data_files_count=0)


def test_writer_creates_namespace_and_table_on_first_append() -> None:
    fake = _FakeCatalog()
    writer = QueryLogIcebergWriter(catalog=fake, namespace="cryo_internal", table_name="queries")
    writer.append(
        [
            {
                "query_id": "q1",
                "source_id": "s1",
                "executed_at": datetime.now(UTC),
            }
        ]
    )
    assert fake.create_namespace_calls == 1
    assert len(fake.create_table_calls) == 1
    assert fake.create_table_calls[0][0] == "cryo_internal.queries"
    assert fake.last_appended[0]["query_id"] == "q1"


def test_writer_does_not_recreate_table_on_repeat_appends() -> None:
    """Cache the table handle: PyIceberg's create_table_if_not_exists has overhead
    (catalog round-trip + metadata validation). Repeat appends skip it.
    """
    fake = _FakeCatalog()
    writer = QueryLogIcebergWriter(catalog=fake, namespace="cryo_internal", table_name="queries")
    for i in range(3):
        writer.append(
            [
                {
                    "query_id": f"q{i}",
                    "source_id": "s1",
                    "executed_at": datetime.now(UTC),
                }
            ]
        )
    assert len(fake.create_table_calls) == 1
    assert len(fake.last_appended) == 3


# ---------------------------------------------------------------------------
# Default buffer size sanity
# ---------------------------------------------------------------------------


def test_default_maxlen_is_bounded() -> None:
    """Sanity check on the buffer's hard cap. 250k messages at ~1 KB each is ~250 MB per
    source -- the curator is expected to drain every tick so this is a backstop, not the
    steady state. If you change this number, also re-check the curator's drain rate.
    """
    assert DEFAULT_MAXLEN <= 500_000
    assert DEFAULT_MAXLEN >= 100_000


# ---------------------------------------------------------------------------
# Pull-interval validation in parse
# ---------------------------------------------------------------------------


def test_parse_clamps_pull_interval_too_low() -> None:
    """Pull cadence < 10s would hammer Trino for nothing -- clamp at 10s."""
    cfg = parse_query_log_config({"enabled": True, "pull_interval_seconds": 1})
    assert cfg is not None
    assert cfg.pull_interval_seconds == 10


def test_parse_clamps_pull_interval_too_high() -> None:
    """Pull cadence > 1h would miss the 5-min Trino ring buffer -- clamp at 3600s."""
    cfg = parse_query_log_config({"enabled": True, "pull_interval_seconds": 999_999})
    assert cfg is not None
    assert cfg.pull_interval_seconds == 3600


# ---------------------------------------------------------------------------
# Multi-coordinator expansion
# ---------------------------------------------------------------------------


def test_telemetry_coordinators_are_treated_as_ordered_dedup_set() -> None:
    """The curator polls every endpoint every tick (no round-robin) and dedupes by
    query_id. Order matters for log readability; duplicates are not de-duped by the
    config (the caller is expected to be sane)."""
    cfg = parse_query_log_config(
        {
            "enabled": True,
            "telemetry_coordinators": [
                "http://trino-1:8080",
                "http://trino-2:8080",
                "http://trino-3:8080",
            ],
        }
    )
    assert cfg is not None
    assert len(cfg.telemetry_coordinators) == 3
    assert cfg.telemetry_coordinators[0] == "http://trino-1:8080"
    assert cfg.telemetry_coordinators[2] == "http://trino-3:8080"


# ---------------------------------------------------------------------------
# QueryLogConfig dataclass is frozen
# ---------------------------------------------------------------------------


def test_config_is_frozen() -> None:
    """The config object is part of the curator's read-only contract; an accidental
    mutation in a curator method would silently corrupt per-tick behaviour.
    """
    import dataclasses

    cfg = parse_query_log_config({"enabled": True})
    assert cfg is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.namespace = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Curator behaviour when destination has no linked Source
# ---------------------------------------------------------------------------


def test_curator_runs_without_source_still_archives_to_iceberg() -> None:
    """A destination without a linked Source can still archive to Iceberg (no FK)
    and skip the curated-Postgres spill. This is the bootstrap case for the
    per-destination curator before the Destination<->Source FK lands.
    """
    import asyncio
    from types import SimpleNamespace

    from cryo.telemetry.curator import CuratorSettings, QueryLogCurator

    destination = SimpleNamespace(
        id=uuid4(),
        name="trino-a",
        query_log_config={"enabled": True, "mode": "trino_system_runtime"},
        query_log_retention_days=30,
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None

    fake_writer = _FakeWriter()
    fake_ingester = _FakeIngester(
        rows=[
            {
                "query_id": "q1",
                "query": "SELECT * FROM analytics.orders",
                "executed_at": datetime.now(UTC).isoformat(),
            }
        ]
    )

    curator = QueryLogCurator(
        writer=fake_writer,  # type: ignore[arg-type]
        destination=destination,
        source=None,  # the bootstrap case
        config=config,
        trino_ingester=fake_ingester,
        settings=CuratorSettings(),
    )

    # Minimal session stand-in: just an object with ``add`` and ``commit`` so the
    # watermark-advance path can run end-to-end. We're not asserting the watermark
    # value here -- just that the tick survives without a real DB session.
    session = _FakeSession()

    result = asyncio.run(curator.run(session))  # type: ignore[arg-type]
    assert result.ok, result.error
    assert result.rows_fetched == 1
    assert result.rows_sampled_in == 1
    assert result.iceberg_appended == 1
    assert result.curated_inserted == 0  # no Source => skip the FK-gated spill
    assert fake_writer.append_calls[0]["query_id"] == "q1"


def test_curator_surfaces_failure_instead_of_silent_drop() -> None:
    """If Iceberg append fails, the result's ``error`` is set and the curator returns
    a failed result -- it never silently swallows an error. The tick continues for
    other destinations; the scheduler logs and moves on.
    """
    import asyncio
    from types import SimpleNamespace

    from cryo.telemetry.curator import CuratorSettings, QueryLogCurator
    from cryo.telemetry.iceberg_writer import QueryLogAppendResult

    destination = SimpleNamespace(
        id=uuid4(),
        name="trino-a",
        query_log_config={"enabled": True},
        query_log_retention_days=30,
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None

    class _FailingWriter(_FakeWriter):
        def append(self, rows):
            self.append_calls.extend(rows)
            return QueryLogAppendResult(
                rows_attempted=len(rows),
                rows_appended=0,
                table_identifier=self.table_identifier,
                error="simulated catalog outage",
            )

    fake_ingester = _FakeIngester(
        rows=[{"query_id": "q1", "query": "SELECT 1", "executed_at": datetime.now(UTC).isoformat()}]
    )
    curator = QueryLogCurator(
        writer=_FailingWriter(),  # type: ignore[arg-type]
        destination=destination,
        source=None,
        config=config,
        trino_ingester=fake_ingester,
        settings=CuratorSettings(),
    )
    session = _FakeSession()
    result = asyncio.run(curator.run(session))  # type: ignore[arg-type]
    assert not result.ok
    assert "simulated catalog outage" in (result.error or "")


def test_curator_enrichment_calls_fetch_query_stats_when_opted_in() -> None:
    """fetch_stats_for_curated=true makes the curator call the ingester's
    fetch_query_stats for each worthy row and fold the result in via
    enrich_row_with_query_stats -- verified through the real _enrich_with_query_stats
    method, not a reimplementation of its logic."""
    import asyncio
    from types import SimpleNamespace

    from cryo.telemetry.curator import CuratorSettings, QueryLogCurator

    class _StatsIngester(_FakeIngester):
        def __init__(self, rows: list[dict]) -> None:
            super().__init__(rows)
            self.stats_calls: list[str] = []

        async def fetch_query_stats(self, query_id, *, client=None):  # type: ignore[no-untyped-def]
            self.stats_calls.append(query_id)
            return {
                "queryStats": {"physicalInputDataSize": "1000B", "physicalInputPositions": 10},
                "inputs": [{"catalogName": "sample", "schema": "s", "table": "t"}],
            }

    destination = SimpleNamespace(
        id=uuid4(),
        name="trino-a",
        query_log_config={"enabled": True, "fetch_stats_for_curated": True},
        query_log_retention_days=30,
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None
    assert config.fetch_stats_for_curated is True

    fake_ingester = _StatsIngester(
        rows=[{"query_id": "q1", "query": "SELECT 1", "executed_at": datetime.now(UTC).isoformat()}]
    )
    curator = QueryLogCurator(
        writer=_FakeWriter(),  # type: ignore[arg-type]
        destination=destination,
        source=None,  # curated spill is skipped without a Source, but enrichment is
        # exercised directly below regardless of that gate.
        config=config,
        trino_ingester=fake_ingester,
        settings=CuratorSettings(),
    )

    rows = [{"query_id": "q1", "bytes_read": None, "rows_read": None, "files_read": None}]
    enriched = asyncio.run(curator._enrich_with_query_stats(rows))

    assert fake_ingester.stats_calls == ["q1"]
    assert enriched[0]["bytes_read"] == 1000
    assert enriched[0]["rows_read"] == 10
    assert enriched[0]["files_read"] == 1


def test_curator_enrichment_is_a_noop_when_ingester_lacks_fetch_query_stats() -> None:
    """The common case: an ingester (or test double) with no fetch_query_stats method must
    not raise -- rows pass through unchanged, same as fetch_stats_for_curated=False."""
    import asyncio
    from types import SimpleNamespace

    from cryo.telemetry.curator import CuratorSettings, QueryLogCurator

    destination = SimpleNamespace(
        id=uuid4(),
        name="trino-a",
        query_log_config={"enabled": True, "fetch_stats_for_curated": True},
        query_log_retention_days=30,
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None

    fake_ingester = _FakeIngester(rows=[])  # no fetch_query_stats defined
    curator = QueryLogCurator(
        writer=_FakeWriter(),  # type: ignore[arg-type]
        destination=destination,
        source=None,
        config=config,
        trino_ingester=fake_ingester,
        settings=CuratorSettings(),
    )

    rows = [{"query_id": "q1", "bytes_read": None}]
    enriched = asyncio.run(curator._enrich_with_query_stats(rows))
    assert enriched == rows


def test_curator_enrichment_respects_the_per_tick_cap() -> None:
    """max_stats_enrichment_per_tick bounds how many rows get a real HTTP call, even when
    the curated-worthy set is larger -- protects Trino from a burst of per-query calls on
    a chatty tick."""
    import asyncio
    from types import SimpleNamespace

    from cryo.telemetry.curator import CuratorSettings, QueryLogCurator

    class _StatsIngester(_FakeIngester):
        def __init__(self, rows: list[dict]) -> None:
            super().__init__(rows)
            self.stats_calls: list[str] = []

        async def fetch_query_stats(self, query_id, *, client=None):  # type: ignore[no-untyped-def]
            self.stats_calls.append(query_id)
            return {"queryStats": {"physicalInputDataSize": "1B"}}

    destination = SimpleNamespace(
        id=uuid4(),
        name="trino-a",
        query_log_config={
            "enabled": True,
            "fetch_stats_for_curated": True,
            "max_stats_enrichment_per_tick": 2,
        },
        query_log_retention_days=30,
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None
    assert config.max_stats_enrichment_per_tick == 2

    fake_ingester = _StatsIngester(rows=[])
    curator = QueryLogCurator(
        writer=_FakeWriter(),  # type: ignore[arg-type]
        destination=destination,
        source=None,
        config=config,
        trino_ingester=fake_ingester,
        settings=CuratorSettings(),
    )

    rows = [{"query_id": f"q{i}", "bytes_read": None} for i in range(5)]
    asyncio.run(curator._enrich_with_query_stats(rows))
    assert len(fake_ingester.stats_calls) == 2


def test_curator_dedupe_across_multi_coordinator_poll() -> None:
    """Multi-coordinator: the same query_id reported by two coordinators should
    be written to Iceberg exactly once. The curator polls each endpoint and dedupes
    by query_id, keeping the row with the latest executed_at.
    """
    import asyncio
    from types import SimpleNamespace

    from cryo.telemetry.curator import CuratorSettings, QueryLogCurator

    destination = SimpleNamespace(
        id=uuid4(),
        name="trino-a",
        query_log_config={
            "enabled": True,
            "telemetry_coordinators": ["http://c1:8080", "http://c2:8080"],
        },
        query_log_retention_days=30,
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None
    assert len(config.telemetry_coordinators) == 2

    fake_writer = _FakeWriter()
    now = datetime.now(UTC).isoformat()
    # Same query_id on both coordinators
    fake_ingester = _FakeIngester(
        rows=[{"query_id": "q-shared", "query": "SELECT 1", "executed_at": now}]
    )
    curator = QueryLogCurator(
        writer=fake_writer,  # type: ignore[arg-type]
        destination=destination,
        source=None,
        config=config,
        trino_ingester=fake_ingester,
        settings=CuratorSettings(),
    )
    session = _FakeSession()
    result = asyncio.run(curator.run(session))  # type: ignore[arg-type]
    assert result.ok, result.error
    # Two endpoints queried, same row both times -- dedupe collapses to one Iceberg row.
    assert result.iceberg_appended == 1
    assert len(fake_writer.append_calls) == 1


# ---------------------------------------------------------------------------
# _spill_curated's real Postgres path (source is not None) -- everything above uses
# source=None specifically to avoid touching the DB, which meant a real bug (every row in
# a multi-row curated batch getting the SAME query_id -- a stale closure-over-loop-variable
# bug from the enrichment refactor) shipped to production undetected. These use the real
# `session` fixture (Postgres-shaped) so QueryLog construction and _resolve_table's real
# SELECT actually run.
# ---------------------------------------------------------------------------


def test_curated_rows_keep_their_own_query_id_not_the_last_rows(session) -> None:
    """Regression: _spill_curated used to build `worthy_rows` in one loop (binding `qid`
    as a loop-local) then construct QueryLog objects in a second loop still referencing
    that same `qid` -- every row in the batch silently got the last row's query_id.
    Confirmed live 2026-09-28: 55 curated rows, 1 distinct query_id, before this fix."""
    import asyncio
    from types import SimpleNamespace

    from cryo.db.models import Source
    from cryo.telemetry.curator import CuratorSettings, QueryLogCurator

    source = Source(id=uuid4(), name="s", type="rest", config={})
    session.add(source)
    session.flush()

    destination = SimpleNamespace(
        id=uuid4(), name="d", query_log_config={"enabled": True}, query_log_retention_days=30
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None

    now = datetime.now(UTC)
    rows = [
        {
            "query_id": f"q{i}",
            "query": "SELECT 1",
            "query_text": "SELECT 1",
            "executed_at": now,
            "state": "FINISHED",
        }
        for i in range(5)
    ]
    fake_ingester = _FakeIngester(rows=rows)
    curator = QueryLogCurator(
        writer=_FakeWriter(),  # type: ignore[arg-type]
        destination=destination,
        source=source,
        config=config,
        trino_ingester=fake_ingester,
        settings=CuratorSettings(curated_sample_rate=1.0),
    )

    inserted = asyncio.run(curator._spill_curated(session, rows))
    session.flush()

    assert inserted == 5
    from cryo.db.models import QueryLog

    stored_ids = {row.query_id for row in session.query(QueryLog).all()}
    assert stored_ids == {f"q{i}" for i in range(5)}


def test_resolve_table_prefers_trino_inputs_over_query_text_regex(session) -> None:
    """A row enriched with real Trino inputs[] must resolve via that, not the fragile
    first-table-in-SQL regex -- confirmed live that regex only matches the first table
    reference and breaks on the multi-table queries this fleet actually runs (up to 100
    inputs on one real production query)."""
    from types import SimpleNamespace

    from cryo.db.models import Source, Table as TableModel
    from cryo.telemetry.curator import CuratorSettings, QueryLogCurator

    source = Source(id=uuid4(), name="s", type="rest", config={})
    session.add(source)
    session.flush()
    real_table = TableModel(
        id=uuid4(), source_id=source.id, schema_name="analytics", table_name="silver_dbt_orders",
        is_active=True,
    )
    session.add(real_table)
    session.flush()

    destination = SimpleNamespace(
        id=uuid4(), name="d", query_log_config={"enabled": True}, query_log_retention_days=30
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None
    curator = QueryLogCurator(
        writer=_FakeWriter(),  # type: ignore[arg-type]
        destination=destination,
        source=source,
        config=config,
        trino_ingester=_FakeIngester(rows=[]),
        settings=CuratorSettings(),
    )

    row = {
        # A join-shaped query text the regex would resolve to the WRONG table (the first
        # one named), while _trino_inputs correctly names the real one queried.
        "query_text": "SELECT * FROM wrong_schema.wrong_table JOIN analytics.silver_dbt_orders ON 1=1",
        "_trino_inputs": [
            {"catalog": "sample", "schema": "analytics", "table": "silver_dbt_orders$files"},
            {"catalog": "sample", "schema": "analytics", "table": "silver_dbt_orders"},
        ],
    }
    resolved = curator._resolve_table(session, row)
    assert resolved is not None
    assert resolved.id == real_table.id


def test_resolve_table_skips_metadata_table_entries_in_trino_inputs(session) -> None:
    """inputs[] includes Trino's own metadata-table reads (e.g. a table's $files) as
    separate entries -- these must never be treated as the real table."""
    from types import SimpleNamespace

    from cryo.db.models import Source
    from cryo.telemetry.curator import CuratorSettings, QueryLogCurator

    source = Source(id=uuid4(), name="s", type="rest", config={})
    session.add(source)
    session.flush()

    destination = SimpleNamespace(
        id=uuid4(), name="d", query_log_config={"enabled": True}, query_log_retention_days=30
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None
    curator = QueryLogCurator(
        writer=_FakeWriter(),  # type: ignore[arg-type]
        destination=destination,
        source=source,
        config=config,
        trino_ingester=_FakeIngester(rows=[]),
        settings=CuratorSettings(),
    )

    row = {
        "query_text": "",
        "_trino_inputs": [{"catalog": "sample", "schema": "s", "table": "t$files"}],
    }
    assert curator._resolve_table(session, row) is None


def test_resolve_table_falls_back_to_query_text_when_no_trino_inputs(session) -> None:
    """A row with no enrichment (fetch_stats_for_curated off) keeps the pre-existing
    regex-based resolution -- this fix must not regress that destination's behavior."""
    from types import SimpleNamespace

    from cryo.db.models import Source, Table as TableModel
    from cryo.telemetry.curator import CuratorSettings, QueryLogCurator

    source = Source(id=uuid4(), name="s", type="rest", config={})
    session.add(source)
    session.flush()
    real_table = TableModel(
        id=uuid4(), source_id=source.id, schema_name="analytics", table_name="orders",
        is_active=True,
    )
    session.add(real_table)
    session.flush()

    destination = SimpleNamespace(
        id=uuid4(), name="d", query_log_config={"enabled": True}, query_log_retention_days=30
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None
    curator = QueryLogCurator(
        writer=_FakeWriter(),  # type: ignore[arg-type]
        destination=destination,
        source=source,
        config=config,
        trino_ingester=_FakeIngester(rows=[]),
        settings=CuratorSettings(),
    )

    row = {"query_text": "SELECT * FROM analytics.orders"}
    resolved = curator._resolve_table(session, row)
    assert resolved is not None
    assert resolved.id == real_table.id


# ---------------------------------------------------------------------------
# Auth plumbing (): the curator must authenticate its fetch against
# system.runtime.queries the same way every other Trino query against this destination
# does, or a coordinator with basic/LDAP auth silently 401s on every tick.
# ---------------------------------------------------------------------------


def test_build_ingester_extracts_basic_auth_from_destination_config() -> None:
    from types import SimpleNamespace

    from cryo.telemetry.curator import _build_ingester

    destination = SimpleNamespace(
        config={
            "auth": {"type": "basic", "username": "dbt_user", "password": "s3cr3t"},
            "coordinators": [{"host": "trino.example.com", "port": 443, "http_scheme": "https"}],
        },
        query_log_config={"enabled": True},
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None

    ingester = _build_ingester(destination, config, source=None)
    assert ingester is not None
    assert ingester.user == "dbt_user"
    assert ingester.auth == ("dbt_user", "s3cr3t")
    assert ingester.trino_url == "https://trino.example.com:443"


def test_build_ingester_has_no_auth_when_destination_config_has_none() -> None:
    """A coordinator running in trusted-network mode (no auth configured) should still
    work -- ingester.auth must be None, not crash on a missing password.
    """
    from types import SimpleNamespace

    from cryo.telemetry.curator import _build_ingester

    destination = SimpleNamespace(
        config={"coordinators": [{"host": "trino.example.com", "port": 8080}]},
        query_log_config={"enabled": True},
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None

    ingester = _build_ingester(destination, config, source=None)
    assert ingester is not None
    assert ingester.auth is None
    assert ingester.user == "cryo"


def test_build_ingester_respects_verify_ssl_false() -> None:
    from types import SimpleNamespace

    from cryo.telemetry.curator import _build_ingester

    destination = SimpleNamespace(
        config={
            "auth": {"type": "basic", "username": "u", "password": "p"},
            "coordinators": [{"host": "trino.example.com", "port": 443, "http_scheme": "https"}],
            "verify_ssl": False,
        },
        query_log_config={"enabled": True},
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None

    ingester = _build_ingester(destination, config, source=None)
    assert ingester is not None
    assert ingester.verify_ssl is False


def test_multi_coordinator_override_url_preserves_auth() -> None:
    """The per-endpoint clone used by multi-coordinator destinations must carry the same
    credentials as the base ingester -- previously it dropped password/verify_ssl entirely,
    so a multi-coordinator destination with auth enabled would 401 on every endpoint.
    """
    from types import SimpleNamespace

    from cryo.telemetry.curator import _build_ingester, _MultiCoordinatorIngester

    destination = SimpleNamespace(
        config={
            "auth": {"type": "basic", "username": "dbt_user", "password": "s3cr3t"},
            "coordinators": [{"host": "c1.example.com", "port": 443, "http_scheme": "https"}],
            "verify_ssl": False,
        },
        query_log_config={
            "enabled": True,
            "telemetry_coordinators": ["https://c2.example.com:443"],
        },
    )
    config = parse_query_log_config(destination.query_log_config)
    assert config is not None

    ingester = _build_ingester(destination, config, source=None)
    assert isinstance(ingester, _MultiCoordinatorIngester)

    cloned = ingester.override_url("https://c2.example.com:443")
    assert cloned.auth == ("dbt_user", "s3cr3t")
    assert cloned.verify_ssl is False


# ---------------------------------------------------------------------------
# Test doubles for the curator
# ---------------------------------------------------------------------------


class _FakeWriter:
    """Stand-in for ``QueryLogIcebergWriter`` -- records calls, no I/O."""

    def __init__(self) -> None:
        self.append_calls: list[dict] = []
        self.expire_calls = 0

    @property
    def table_identifier(self) -> str:
        return "cryo_internal.queries"

    def append(self, rows: list[dict]):  # type: ignore[no-untyped-def]
        from cryo.telemetry.iceberg_writer import QueryLogAppendResult

        self.append_calls.extend(rows)
        return QueryLogAppendResult(
            rows_attempted=len(rows),
            rows_appended=len(rows),
            table_identifier=self.table_identifier,
        )

    def expire_snapshots_older_than(self, days):  # type: ignore[no-untyped-def]
        from cryo.telemetry.iceberg_writer import QueryLogRetentionResult

        self.expire_calls += 1
        return QueryLogRetentionResult(table_identifier=self.table_identifier)


class _FakeIngester:
    """Stand-in for ``TrinoQueryIngester`` -- returns the same row set on every fetch."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = list(rows)

    async def fetch_recent_queries(self, *, since=None, limit=1000):  # type: ignore[no-untyped-def]
        return list(self._rows)

    def override_url(self, url: str):  # type: ignore[no-untyped-def]
        return self


class _FakeSession:
    """Stand-in for a SQLAlchemy ``Session`` -- records adds + commits, no real I/O.

    Enough for the curator's watermark-advance path. Real SQL goes through the
    integration tests in tests/test_datalake_api.py; this double exists so the curator
    unit tests can run without spinning up Postgres.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1


# ---------------------------------------------------------------------------
# Sample-rate defaults
# ---------------------------------------------------------------------------


def test_default_sample_rate_is_one() -> None:
    """No sample_rate = archive everything. A 1M-queries/day destination will overflow
    the buffer if the curator can't keep up, but that's the curator's problem to solve
    via overlap + retention -- not silent under-sampling.
    """
    cfg = parse_query_log_config({"enabled": True})
    assert cfg is not None
    assert cfg.sample_rate is None
    # And the helper agrees: None means "include"
    assert sample_query("any-qid", None) is True
