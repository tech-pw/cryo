"""TrinoQueryIngester's password/verify_ssl args were defined in Settings
(telemetry_trino_user/password/ssl_verify) and documented ("unset means no BasicAuthentication
is attempted") but never actually passed to the ingester by its only call site
(cryo/scheduler/service.py::_maybe_ingest_telemetry) -- every request only ever carried the
trusted-network X-Trino-User header, so a coordinator with real password/LDAP auth enabled
always 401'd regardless of what credentials were configured. Real production repro: 2026-07-14,
trino-prod-replica-3.penpencil.co rejected every telemetry tick with 401 until this was found.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler.service import SchedulerService
from cryo.telemetry.ingestion import TrinoQueryError, TrinoQueryIngester
from tests.test_scheduler_telemetry import _bound_session_factory, _NullStore, _seed_source


def test_no_password_sends_no_basic_auth() -> None:
    ingester = TrinoQueryIngester("https://trino.example.com", user="cryo")

    assert ingester.auth is None


def test_password_builds_basic_auth_tuple() -> None:
    ingester = TrinoQueryIngester(
        "https://trino.example.com", user="phoenix_user", password="secret"
    )

    assert ingester.auth == ("phoenix_user", "secret")


def test_verify_ssl_defaults_true() -> None:
    assert TrinoQueryIngester("https://trino.example.com").verify_ssl is True


def test_verify_ssl_can_be_disabled() -> None:
    assert TrinoQueryIngester("https://trino.example.com", verify_ssl=False).verify_ssl is False


def test_fetch_recent_queries_passes_auth_and_verify_to_http_client() -> None:
    ingester = TrinoQueryIngester(
        "https://trino.example.com",
        user="phoenix_user",
        password="secret",
        verify_ssl=False,
    )
    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [], "columns": []}

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
            return _FakeResponse()

    with patch("cryo.telemetry.ingestion.httpx.AsyncClient", _FakeClient):
        asyncio.run(ingester.fetch_recent_queries())

    assert captured["auth"] == ("phoenix_user", "secret")
    assert captured["verify"] is False


def test_scheduler_wires_telemetry_credentials_into_ingester(session, monkeypatch) -> None:
    """The scheduler's call site must actually pass settings.telemetry_trino_user/password/
    ssl_verify through, not just leave TrinoQueryIngester on its no-auth defaults -- this is
    the exact gap that caused every real telemetry tick to 401."""
    _seed_source(session)  # no query_log config -> takes the legacy TrinoQueryIngester path
    # 2026-09-03: _maybe_ingest_telemetry now opens a fresh SessionLocal() per source (see
    # that method's own docstring) -- flush-only seed data is invisible to that sibling
    # session until committed.
    session.commit()

    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    fake_settings = type(
        "FakeSettings",
        (),
        {
            "telemetry_trino_url": "https://trino-prod-replica-3.penpencil.co:443",
            "telemetry_trino_user": "phoenix_user",
            "telemetry_trino_password": "secret",
            "telemetry_trino_ssl_verify": False,
            "telemetry_ingest_on_startup": True,
        },
    )()
    monkeypatch.setattr("cryo.config.get_settings", lambda: fake_settings)

    captured_kwargs: dict[str, object] = {}

    class _CapturingIngester:
        def __init__(self, url: str, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

        async def fetch_recent_queries(self, *a: object, **k: object) -> list[object]:
            return []

    monkeypatch.setattr("cryo.telemetry.ingestion.TrinoQueryIngester", _CapturingIngester)
    monkeypatch.setattr(
        "cryo.scheduler.service._source_for_destination", lambda session, destination: None
    )

    service = SchedulerService(_NullStore(), InMemoryMaintenanceQueue())
    service._maybe_ingest_telemetry(datetime.now(UTC))

    assert captured_kwargs.get("user") == "phoenix_user"
    assert captured_kwargs.get("password") == "secret"
    assert captured_kwargs.get("verify_ssl") is False


# ---------------------------------------------------------------------------
# Query-level error detection (PDAE-8277)
#
# Trino's /v1/statement API returns HTTP 200 even when the query itself fails (bad SQL,
# COLUMN_NOT_FOUND, permission denied, ...) -- the failure lives in the JSON body's "error"
# key. Before this, fetch_recent_queries only checked response.raise_for_status() (HTTP
# status), so a query-level error page -- which has neither "data" nor "nextUri" -- looked
# identical to "no more rows" and silently returned []. Real repro: this SELECT queried
# execution_time_ms/physical_input_bytes/... columns that don't exist on Trino 480 in prod,
# so Trino-Prod's curator fetched 0 rows on every tick with zero visible error.
# ---------------------------------------------------------------------------


def _fake_client(response_json: dict[str, object]):
    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return response_json

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
            return _FakeResponse()

    return _FakeClient


def test_fetch_recent_queries_raises_on_trino_query_level_error() -> None:
    """A COLUMN_NOT_FOUND-shaped error body must raise, not be swallowed as an empty list."""
    ingester = TrinoQueryIngester("https://trino.example.com")
    error_body = {
        "error": {
            "message": "line 13:13: Column 'execution_time_ms' cannot be resolved",
            "errorName": "COLUMN_NOT_FOUND",
            "errorCode": 47,
        }
    }

    with patch("cryo.telemetry.ingestion.httpx.AsyncClient", _fake_client(error_body)):
        with pytest.raises(TrinoQueryError) as exc_info:
            asyncio.run(ingester.fetch_recent_queries())

    assert "COLUMN_NOT_FOUND" in str(exc_info.value)
    assert "execution_time_ms" in str(exc_info.value)


def test_fetch_recent_queries_returns_rows_on_success() -> None:
    """Sanity check: a normal, error-free response still returns rows as before."""
    ingester = TrinoQueryIngester("https://trino.example.com")
    success_body = {
        "columns": [{"name": "query_id"}, {"name": "state"}],
        "data": [["q1", "FINISHED"]],
    }

    with patch("cryo.telemetry.ingestion.httpx.AsyncClient", _fake_client(success_body)):
        rows = asyncio.run(ingester.fetch_recent_queries())

    assert rows == [{"query_id": "q1", "state": "FINISHED"}]


def test_trino_query_error_message_includes_error_name_and_text() -> None:
    exc = TrinoQueryError({"message": "permission denied", "errorName": "PERMISSION_DENIED"})
    assert "PERMISSION_DENIED" in str(exc)
    assert "permission denied" in str(exc)
