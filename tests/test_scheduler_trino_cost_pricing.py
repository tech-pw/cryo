"""Tests for SchedulerService._maybe_price_trino_scan_cost -- derives a real $/GiB Trino
compute price from the trino-db namespace's actual Grafana Athena CUR cost divided by real
bytes scanned that day, and writes it onto QueryCostAttributionDaily.estimated_scan_cost_usd.
Off by default (Settings.trino_cost_pricing_enabled) and requires CRYO_GRAFANA_TOKEN, same
fail-safe-open contract as the sibling _maybe_check_cost_ceiling tick.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import select

from cryo.db.models import QueryCostAttributionDaily, Source
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler.service import SchedulerService


class _FakeStore:
    def list_due_presets(self, now):
        return []

    def list_tables(self):
        return []

    def list_presets(self):
        return []

    def list_rules(self):
        return []

    def list_assignments(self):
        return []

    def list_destinations(self):
        return []

    def persist_run(self, run):
        pass

    def mark_preset_run(self, preset_id, run_at):
        pass


def _service() -> SchedulerService:
    return SchedulerService(_FakeStore(), InMemoryMaintenanceQueue())


class _FakeSettings:
    trino_cost_pricing_enabled = True
    trino_cost_pricing_namespace = "trino-db"
    trino_cost_pricing_lookback_days = 10
    cost_ceiling_cluster_name = "data-platform-eks"
    grafana_base_url = "https://grafana.example.com"


class _DisabledSettings(_FakeSettings):
    trino_cost_pricing_enabled = False


def _grafana_range_response(rows: list[tuple[str, float, float]]) -> dict:
    days = [r[0] for r in rows]
    used = [r[1] for r in rows]
    unused = [r[2] for r in rows]
    return {
        "results": {
            "A": {
                "frames": [
                    {
                        "schema": {
                            "fields": [
                                {"name": "day"},
                                {"name": "used_cost"},
                                {"name": "unused_cost"},
                            ]
                        },
                        "data": {"values": [days, used, unused]},
                    }
                ]
            }
        }
    }


def _mock_httpx_post(payload: dict):
    def _post(url, headers=None, json=None, timeout=None):
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    return _post


def _seed_query_cost_row(session, *, day: date, bytes_scanned: int) -> None:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="hive", config={})
    session.add(source)
    session.flush()
    session.add(
        QueryCostAttributionDaily(
            source_id=source.id,
            day=day,
            client="Metabase",
            schema_name="cdp_revenue",
            query_count=10,
            failed_query_count=0,
            physical_bytes_scanned=bytes_scanned,
        )
    )
    session.commit()


def test_noop_when_pricing_disabled(session, monkeypatch) -> None:
    monkeypatch.setattr("cryo.config.get_settings", lambda: _DisabledSettings())
    service = _service()

    with patch("httpx.post") as mock_post:
        service._maybe_price_trino_scan_cost(datetime.now(UTC))

    mock_post.assert_not_called()
    assert service._last_trino_cost_pricing is None


def test_noop_when_grafana_token_not_set(session, monkeypatch) -> None:
    monkeypatch.setattr("cryo.config.get_settings", lambda: _FakeSettings())
    monkeypatch.delenv("CRYO_GRAFANA_TOKEN", raising=False)
    service = _service()

    with patch("httpx.post") as mock_post:
        service._maybe_price_trino_scan_cost(datetime.now(UTC))

    mock_post.assert_not_called()
    assert service._last_trino_cost_pricing is None


def test_prices_and_writes_estimated_scan_cost(session, monkeypatch) -> None:
    monkeypatch.setattr("cryo.config.get_settings", lambda: _FakeSettings())
    monkeypatch.setenv("CRYO_GRAFANA_TOKEN", "tok")
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", lambda: session)

    day = date(2026, 8, 14)
    gib = 1_073_741_824
    _seed_query_cost_row(session, day=day, bytes_scanned=50 * gib)

    payload = _grafana_range_response([("2026-08-14", 40.0, 60.0)])  # $100 total that day
    service = _service()

    with patch("httpx.post", side_effect=_mock_httpx_post(payload)):
        service._maybe_price_trino_scan_cost(datetime(2026, 8, 17, tzinfo=UTC))

    assert service._last_trino_cost_pricing is not None
    row = session.scalar(
        select(QueryCostAttributionDaily).where(QueryCostAttributionDaily.day == day)
    )
    assert row.estimated_scan_cost_usd is not None
    # $100 / 50 GiB = $2/GiB; this row is the only one that day, so it gets the full $100.
    assert row.estimated_scan_cost_usd == 100.0


def test_survives_grafana_fetch_failure(session, monkeypatch) -> None:
    """A Grafana outage/auth failure must not raise out of the scheduler tick -- fail
    safe-open, matching cost_ceiling's degraded-dependency contract."""
    monkeypatch.setattr("cryo.config.get_settings", lambda: _FakeSettings())
    monkeypatch.setenv("CRYO_GRAFANA_TOKEN", "tok")

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    service = _service()
    with patch("httpx.post", side_effect=_raise):
        service._maybe_price_trino_scan_cost(datetime.now(UTC))

    # Failure means the tick didn't complete -- watermark must not have advanced.
    assert service._last_trino_cost_pricing is None


def test_second_tick_within_a_day_is_a_noop(session, monkeypatch) -> None:
    monkeypatch.setattr("cryo.config.get_settings", lambda: _FakeSettings())
    monkeypatch.setenv("CRYO_GRAFANA_TOKEN", "tok")
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", lambda: session)

    day = date(2026, 8, 14)
    _seed_query_cost_row(session, day=day, bytes_scanned=50 * 1_073_741_824)
    payload = _grafana_range_response([("2026-08-14", 40.0, 60.0)])
    service = _service()

    with patch("httpx.post", side_effect=_mock_httpx_post(payload)) as mock_post:
        service._maybe_price_trino_scan_cost(datetime(2026, 8, 17, 6, tzinfo=UTC))
        assert mock_post.call_count == 1
        service._maybe_price_trino_scan_cost(datetime(2026, 8, 17, 12, tzinfo=UTC))
        # Same day, well under 24h later -- must not re-fetch.
        assert mock_post.call_count == 1
