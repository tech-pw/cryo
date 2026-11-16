"""Run console WebSocket auth via single-use tickets; no bearer token in the URL (#551)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cryo.api import routes
from cryo.api.app import create_app
from cryo.api.deps import get_tenant_session
from cryo.api.security import Principal, Role
from cryo.api.ws_tickets import TICKET_TTL_SECONDS, WsTicketStore
from cryo.config import Settings
from cryo.observability.log_redaction import (
    REDACTED,
    QueryStringCredentialFilter,
    scrub_query_credentials,
)
from cryo.observability.metrics import ws_auth_failures_total
from tests.auth_helpers import VIEWER_HEADERS, auth_header, ensure_test_jwt_secret


class _FakeSession:
    def expunge_all(self) -> None:
        return None


@pytest.fixture
def redis_client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, redis_client: fakeredis.FakeRedis) -> TestClient:
    ensure_test_jwt_secret()
    monkeypatch.setattr(routes.ws_ticket_store, "redis_getter", lambda: redis_client)
    monkeypatch.setattr(routes, "run_visible", lambda *_a, **_k: True)
    monkeypatch.setattr(routes.datalake, "run_jobs_snapshot", lambda *_a, **_k: _snapshot())
    app = create_app(Settings(_env_file=None, environment="test"))

    def fake_session() -> Iterator[_FakeSession]:
        yield _FakeSession()

    app.dependency_overrides[get_tenant_session] = fake_session
    return TestClient(app)


_RUN_ID = uuid.uuid4()


def _snapshot() -> dict[str, Any]:
    return {
        "run": {
            "id": _RUN_ID,
            "kind": "adhoc",
            "status": "succeeded",
            "preset_id": None,
            "trigger": "api",
            "deadline_at": None,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "finished_at": None,
            "summary": {},
        },
        "jobs": [],
    }


def _failures(reason: str) -> float:
    return ws_auth_failures_total.labels(reason=reason)._value.get()


def _issue(client: TestClient, run_id: uuid.UUID = _RUN_ID) -> str:
    response = client.post(
        "/api/v1/ws-tickets", json={"run_id": str(run_id)}, headers=VIEWER_HEADERS
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["expires_in_sec"] == TICKET_TTL_SECONDS
    return str(body["ticket"])


def _assert_rejected(client: TestClient, url: str) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url) as ws:
            ws.receive_json()
    assert exc.value.code == 1008


def test_ticket_issue_requires_auth(client: TestClient) -> None:
    assert client.post("/api/v1/ws-tickets", json={"run_id": str(_RUN_ID)}).status_code == 401
    forbidden = client.post(
        "/api/v1/ws-tickets",
        json={"run_id": str(_RUN_ID)},
        headers=auth_header("evidence_collector"),
    )
    assert forbidden.status_code == 403


def test_ticket_is_stored_hashed_with_ttl(
    client: TestClient, redis_client: fakeredis.FakeRedis
) -> None:
    ticket = _issue(client)
    keys = redis_client.keys("cryo:ws_ticket:*")
    assert len(keys) == 1
    assert ticket.encode() not in keys[0]
    assert 0 < redis_client.ttl(keys[0]) <= TICKET_TTL_SECONDS


def test_ticket_opens_socket_once(client: TestClient) -> None:
    ticket = _issue(client)
    url = f"/api/v1/ws/runs/{_RUN_ID}?once=true&ticket={ticket}"
    with client.websocket_connect(url) as ws:
        payload = ws.receive_json()
    assert payload["type"] == "run_snapshot"

    before = _failures("invalid_ticket")
    _assert_rejected(client, url)
    assert _failures("invalid_ticket") == before + 1


def test_expired_ticket_rejected(client: TestClient, redis_client: fakeredis.FakeRedis) -> None:
    ticket = _issue(client)
    for key in redis_client.keys("cryo:ws_ticket:*"):
        redis_client.delete(key)
    _assert_rejected(client, f"/api/v1/ws/runs/{_RUN_ID}?once=true&ticket={ticket}")


def test_ticket_for_another_run_rejected_and_spent(client: TestClient) -> None:
    ticket = _issue(client, uuid.uuid4())
    before = _failures("run_mismatch")
    _assert_rejected(client, f"/api/v1/ws/runs/{_RUN_ID}?once=true&ticket={ticket}")
    assert _failures("run_mismatch") == before + 1


def test_query_token_rejected_by_default(client: TestClient) -> None:
    token = VIEWER_HEADERS["Authorization"].removeprefix("Bearer ")
    before = _failures("query_token_disabled")
    _assert_rejected(client, f"/api/v1/ws/runs/{_RUN_ID}?once=true&token={token}")
    assert _failures("query_token_disabled") == before + 1


def test_query_token_only_with_compat_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: Settings(_env_file=None, environment="test", ws_query_token_enabled=True),
    )
    token = VIEWER_HEADERS["Authorization"].removeprefix("Bearer ")
    with caplog.at_level(logging.WARNING, logger=routes.logger.name):
        with client.websocket_connect(f"/api/v1/ws/runs/{_RUN_ID}?once=true&token={token}") as ws:
            assert ws.receive_json()["type"] == "run_snapshot"
    assert "deprecated ?token=" in caplog.text
    assert token not in caplog.text


def test_missing_credentials_counted(client: TestClient) -> None:
    before = _failures("missing_credentials")
    _assert_rejected(client, f"/api/v1/ws/runs/{_RUN_ID}?once=true")
    assert _failures("missing_credentials") == before + 1


def test_authorization_header_still_accepted(client: TestClient) -> None:
    with client.websocket_connect(
        f"/api/v1/ws/runs/{_RUN_ID}?once=true", headers=VIEWER_HEADERS
    ) as ws:
        assert ws.receive_json()["type"] == "run_snapshot"


def test_ticket_preserves_principal_scope(redis_client: fakeredis.FakeRedis) -> None:
    store = WsTicketStore(lambda: redis_client)
    tenant = uuid.uuid4()
    principal = Principal(subject="alice", roles=frozenset({Role.OPERATOR}), tenant_id=tenant)
    ticket = store.issue(principal, _RUN_ID)
    restored, reason = store.consume(ticket, _RUN_ID)
    assert reason == "ok"
    assert restored == principal
    assert store.consume(ticket, _RUN_ID) == (None, "invalid_ticket")


def test_ticket_issue_503_without_redis(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.ws_ticket_store, "redis_getter", lambda: None)
    response = client.post(
        "/api/v1/ws-tickets", json={"run_id": str(_RUN_ID)}, headers=VIEWER_HEADERS
    )
    assert response.status_code == 503


def test_access_log_scrubs_ticket_and_token() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "10.0.0.1:1",
            "GET",
            "/api/v1/ws/runs/x?ticket=abc123&once=true&token=eyJhbGc",
            "1.1",
            101,
        ),
        None,
    )
    QueryStringCredentialFilter().filter(record)
    line = record.getMessage()
    assert "abc123" not in line
    assert "eyJhbGc" not in line
    assert f"ticket={REDACTED}&once=true" in line
    assert scrub_query_credentials("/x?a=1") == "/x?a=1"


def test_configure_logging_installs_access_log_filter() -> None:
    create_app(Settings(_env_file=None, environment="test"))
    for name in ("uvicorn.access", "uvicorn.error"):
        assert any(
            isinstance(f, QueryStringCredentialFilter) for f in logging.getLogger(name).filters
        )
