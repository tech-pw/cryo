"""Tests for cryo/reporting/query_cost_signals.py's DoD/WoW Trino query-cost signal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import QueryCostAttributionDaily, Source
from cryo.reporting.query_cost_signals import (
    _top_clients_for_day,
    _totals_for_day,
    compute_query_cost_signal,
)

NOW = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_row(
    session,
    source: Source,
    *,
    day,
    client: str,
    schema_name: str = "gold",
    query_count: int = 10,
    failed_query_count: int = 0,
    physical_bytes_scanned: int = 1024**3,
    estimated_scan_cost_usd: float | None = 1.0,
) -> None:
    session.add(
        QueryCostAttributionDaily(
            source_id=source.id,
            day=day,
            client=client,
            schema_name=schema_name,
            query_count=query_count,
            failed_query_count=failed_query_count,
            physical_bytes_scanned=physical_bytes_scanned,
            estimated_scan_cost_usd=estimated_scan_cost_usd,
        )
    )


# ---------------------------------------------------------------------------
# _totals_for_day
# ---------------------------------------------------------------------------


def test_totals_for_day_none_with_no_rows(session) -> None:
    assert _totals_for_day(session, (NOW - timedelta(days=1)).date()) is None


def test_totals_for_day_sums_across_clients_and_schemas(session) -> None:
    source = _seed_source(session)
    day = (NOW - timedelta(days=1)).date()
    _seed_row(session, source, day=day, client="Metabase", schema_name="gold", query_count=10,
              physical_bytes_scanned=5 * 1024**3, estimated_scan_cost_usd=2.0)
    _seed_row(session, source, day=day, client="dbt-trino", schema_name="silver", query_count=20,
              physical_bytes_scanned=10 * 1024**3, estimated_scan_cost_usd=4.0)
    session.commit()

    totals = _totals_for_day(session, day)

    assert totals is not None
    assert totals.query_count == 30
    assert totals.physical_bytes_scanned == 15 * 1024**3
    assert totals.estimated_scan_cost_usd == 6.0


def test_totals_for_day_handles_null_cost(session) -> None:
    """A day cost_ceiling hasn't priced yet (see derive_trino_scan_cost_prices) leaves
    estimated_scan_cost_usd NULL -- totals must not fabricate a $0 for that."""
    source = _seed_source(session)
    day = (NOW - timedelta(days=1)).date()
    _seed_row(session, source, day=day, client="Metabase", estimated_scan_cost_usd=None)
    session.commit()

    totals = _totals_for_day(session, day)

    assert totals is not None
    assert totals.estimated_scan_cost_usd is None


# ---------------------------------------------------------------------------
# _top_clients_for_day
# ---------------------------------------------------------------------------


def test_top_clients_ranks_priced_clients_by_cost(session) -> None:
    source = _seed_source(session)
    day = (NOW - timedelta(days=1)).date()
    _seed_row(session, source, day=day, client="Metabase", estimated_scan_cost_usd=1.0,
              physical_bytes_scanned=100 * 1024**3)
    _seed_row(session, source, day=day, client="dbt-trino", estimated_scan_cost_usd=5.0,
              physical_bytes_scanned=1 * 1024**3)
    session.commit()

    clients = _top_clients_for_day(session, day)

    assert [c.client for c in clients] == ["dbt-trino", "Metabase"]


def test_top_clients_unpriced_ranked_below_priced(session) -> None:
    source = _seed_source(session)
    day = (NOW - timedelta(days=1)).date()
    _seed_row(session, source, day=day, client="huge-unpriced", estimated_scan_cost_usd=None,
              physical_bytes_scanned=1000 * 1024**3)
    _seed_row(session, source, day=day, client="tiny-priced", estimated_scan_cost_usd=0.01,
              physical_bytes_scanned=1 * 1024**3)
    session.commit()

    clients = _top_clients_for_day(session, day)

    assert [c.client for c in clients] == ["tiny-priced", "huge-unpriced"]


def test_top_clients_merges_multiple_schemas_per_client(session) -> None:
    source = _seed_source(session)
    day = (NOW - timedelta(days=1)).date()
    _seed_row(session, source, day=day, client="Metabase", schema_name="gold",
              query_count=5, estimated_scan_cost_usd=1.0)
    _seed_row(session, source, day=day, client="Metabase", schema_name="silver",
              query_count=7, estimated_scan_cost_usd=2.0)
    session.commit()

    clients = _top_clients_for_day(session, day)

    assert len(clients) == 1
    assert clients[0].query_count == 12
    assert clients[0].estimated_scan_cost_usd == 3.0


# ---------------------------------------------------------------------------
# compute_query_cost_signal
# ---------------------------------------------------------------------------


def test_compute_query_cost_signal_stable_when_flat(session) -> None:
    source = _seed_source(session)
    for delta in (1, 2, 8):
        _seed_row(
            session, source, day=(NOW - timedelta(days=delta)).date(),
            client="Metabase", estimated_scan_cost_usd=10.0,
        )
    session.commit()

    signal = compute_query_cost_signal(session, now=NOW)

    assert signal.direction == "stable"
    assert signal.primary_signal is None


def test_compute_query_cost_signal_dod_spike(session) -> None:
    source = _seed_source(session)
    # yesterday (day -1) well above day -2's baseline -- default DoD spike threshold is 15%.
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date(), client="Metabase",
              estimated_scan_cost_usd=200.0)
    _seed_row(session, source, day=(NOW - timedelta(days=2)).date(), client="Metabase",
              estimated_scan_cost_usd=100.0)
    _seed_row(session, source, day=(NOW - timedelta(days=8)).date(), client="Metabase",
              estimated_scan_cost_usd=190.0)
    session.commit()

    signal = compute_query_cost_signal(session, now=NOW)

    assert signal.direction == "spike"
    assert signal.primary_signal == "dod"
    assert signal.dod_pct is not None
    assert signal.dod_pct > 0
    assert signal.current is not None
    assert signal.current.estimated_scan_cost_usd == 200.0


def test_compute_query_cost_signal_dod_decline(session) -> None:
    source = _seed_source(session)
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date(), client="Metabase",
              estimated_scan_cost_usd=50.0)
    _seed_row(session, source, day=(NOW - timedelta(days=2)).date(), client="Metabase",
              estimated_scan_cost_usd=100.0)
    session.commit()

    signal = compute_query_cost_signal(session, now=NOW)

    assert signal.direction == "decline"
    assert signal.primary_signal == "dod"


def test_compute_query_cost_signal_wow_only_fires_when_dod_is_stable(session) -> None:
    source = _seed_source(session)
    # DoD flat (day -1 vs day -2), but well above day -8 -- WoW should catch this, DoD should not.
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date(), client="Metabase",
              estimated_scan_cost_usd=120.0)
    _seed_row(session, source, day=(NOW - timedelta(days=2)).date(), client="Metabase",
              estimated_scan_cost_usd=119.0)
    _seed_row(session, source, day=(NOW - timedelta(days=8)).date(), client="Metabase",
              estimated_scan_cost_usd=90.0)
    session.commit()

    signal = compute_query_cost_signal(session, now=NOW)

    assert signal.direction == "spike"
    assert signal.primary_signal == "wow"


def test_compute_query_cost_signal_no_baseline_stays_stable(session) -> None:
    source = _seed_source(session)
    # Only "yesterday" has data -- no DoD/WoW baseline to compare against.
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date(), client="Metabase",
              estimated_scan_cost_usd=100.0)
    session.commit()

    signal = compute_query_cost_signal(session, now=NOW)

    assert signal.direction == "stable"
    assert signal.dod_pct is None
    assert signal.wow_pct is None


def test_compute_query_cost_signal_falls_back_to_bytes_when_unpriced(session) -> None:
    """Neither day in the comparison has been priced yet (cost_ceiling ingestion lag) -- the
    signal must still fire off a real bytes-scanned move rather than reporting stable."""
    source = _seed_source(session)
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date(), client="Metabase",
              estimated_scan_cost_usd=None, physical_bytes_scanned=200 * 1024**3)
    _seed_row(session, source, day=(NOW - timedelta(days=2)).date(), client="Metabase",
              estimated_scan_cost_usd=None, physical_bytes_scanned=100 * 1024**3)
    session.commit()

    signal = compute_query_cost_signal(session, now=NOW)

    assert signal.direction == "spike"
    assert signal.primary_signal == "dod"
    assert signal.dod_pct is not None
    assert signal.dod_pct > 0


def test_compute_query_cost_signal_includes_top_clients(session) -> None:
    source = _seed_source(session)
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date(), client="Metabase",
              estimated_scan_cost_usd=10.0)
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date(), client="dbt-trino",
              schema_name="silver", estimated_scan_cost_usd=5.0)
    session.commit()

    signal = compute_query_cost_signal(session, now=NOW)

    assert [c.client for c in signal.top_clients_current] == ["Metabase", "dbt-trino"]
