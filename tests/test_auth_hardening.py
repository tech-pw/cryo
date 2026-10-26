"""#262: sign-in throttling, failed sign-in audit, private API docs, metrics token."""

from __future__ import annotations

import uuid

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from cryo.api import auth_routes, metrics_routes
from cryo.api.app import create_app
from cryo.api.routes_audit import list_fleet_audit
from cryo.config import Settings
from cryo.db.models import AuditLog, AuthSettings, LocalUser
from cryo.security.login_throttle import LoginThrottle, set_login_throttle
from cryo.security.passwords import hash_password
from tests.auth_helpers import ADMIN_HEADERS, VIEWER_HEADERS


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _throttle(redis_client: object | None = None, clock: _Clock | None = None) -> LoginThrottle:
    return LoginThrottle(
        redis_client=redis_client,
        max_account_failures=3,
        max_ip_failures=5,
        window_seconds=60,
        clock=clock or _Clock(),
    )


@pytest.mark.parametrize("backend", ["memory", "redis"])
def test_account_locks_after_limit_and_success_clears_it(backend: str) -> None:
    throttle = _throttle(fakeredis.FakeRedis() if backend == "redis" else None)
    for _ in range(3):
        assert throttle.check(account="alice", client_ip="10.0.0.1").allowed
        throttle.record_failure(account="alice", client_ip="10.0.0.1")
    decision = throttle.check(account="Alice ", client_ip="10.0.0.9")
    assert not decision.allowed
    assert 0 < decision.retry_after_seconds <= 60
    # Other accounts from a different IP are unaffected.
    assert throttle.check(account="bob", client_ip="10.0.0.2").allowed
    throttle.record_success(account="alice")
    assert throttle.check(account="alice", client_ip="10.0.0.9").allowed


@pytest.mark.parametrize("backend", ["memory", "redis"])
def test_ip_limit_spans_accounts(backend: str) -> None:
    throttle = _throttle(fakeredis.FakeRedis() if backend == "redis" else None)
    for index in range(5):
        throttle.record_failure(account=f"user{index}", client_ip="10.0.0.1")
    assert not throttle.check(account="fresh", client_ip="10.0.0.1").allowed
    assert throttle.check(account="fresh", client_ip="10.0.0.2").allowed


def test_redis_window_is_not_extended_by_later_failures() -> None:
    redis_client = fakeredis.FakeRedis()
    throttle = _throttle(redis_client)
    throttle.record_failure(account="alice", client_ip=None)
    key = next(iter(redis_client.scan_iter("cryo:auth:login_failures:acct:*")))
    assert 0 < redis_client.ttl(key) <= 60
    redis_client.expire(key, 10)
    throttle.record_failure(account="alice", client_ip=None)
    assert redis_client.ttl(key) <= 10
    assert int(redis_client.get(key)) == 2
    # Usernames are hashed, never stored.
    assert b"alice" not in key


def test_memory_window_expires() -> None:
    clock = _Clock()
    throttle = _throttle(clock=clock)
    for _ in range(3):
        throttle.record_failure(account="alice", client_ip=None)
    assert not throttle.check(account="alice", client_ip=None).allowed
    clock.now += 61
    assert throttle.check(account="alice", client_ip=None).allowed


def test_redis_outage_falls_back_to_memory() -> None:
    class _Broken:
        def pipeline(self) -> object:
            raise ConnectionError("redis down")

        def delete(self, *_keys: object) -> None:
            raise ConnectionError("redis down")

    throttle = _throttle(_Broken())
    for _ in range(3):
        throttle.record_failure(account="alice", client_ip=None)
    assert not throttle.check(account="alice", client_ip=None).allowed


def test_disabled_throttle_always_allows() -> None:
    throttle = LoginThrottle(
        redis_client=None,
        max_account_failures=1,
        max_ip_failures=1,
        window_seconds=60,
        enabled=False,
    )
    throttle.record_failure(account="alice", client_ip="10.0.0.1")
    assert throttle.check(account="alice", client_ip="10.0.0.1").allowed


@pytest.fixture()
def login_client(monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker) -> TestClient:
    with session_factory() as session:
        session.add(AuthSettings(id=1, password_login_enabled=True))
        session.add(
            LocalUser(
                id=uuid.uuid4(),
                username="alice",
                role="admin",
                password_hash=hash_password("right-pass"),
            )
        )
        session.add(LocalUser(id=uuid.uuid4(), username="carol", role="viewer", disabled=True))
        session.commit()
    monkeypatch.setattr(auth_routes, "SessionLocal", session_factory)
    set_login_throttle(_throttle())
    yield TestClient(create_app(Settings(_env_file=None, environment="test")))
    set_login_throttle(None)


def _failed_rows(session_factory: sessionmaker) -> list[AuditLog]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(AuditLog).where(AuditLog.action == auth_routes.LOGIN_FAILED_ACTION)
            )
        )


def test_login_throttles_after_repeated_failures(
    login_client: TestClient, session_factory: sessionmaker
) -> None:
    for _ in range(3):
        response = login_client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "wrong"}
        )
        assert response.status_code == 401
    # Even the right password is refused while the account is locked.
    locked = login_client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "right-pass"}
    )
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    rows = _failed_rows(session_factory)
    assert len(rows) == 3
    assert {row.actor for row in rows} == {"alice"}
    assert all(row.origin == "password" and row.status == "failed" for row in rows)
    assert all("reason=bad_password" in (row.detail or "") for row in rows)
    assert all("wrong" not in (row.detail or "") for row in rows)


def test_successful_login_clears_account_failures(login_client: TestClient) -> None:
    for _ in range(2):
        login_client.post("/api/v1/auth/login", json={"username": "alice", "password": "nope"})
    ok = login_client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "right-pass"}
    )
    assert ok.status_code == 200
    for _ in range(2):
        response = login_client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "nope"}
        )
        assert response.status_code == 401


def test_unknown_and_disabled_users_are_audited_with_generic_401(
    login_client: TestClient, session_factory: sessionmaker
) -> None:
    unknown = login_client.post("/api/v1/auth/login", json={"username": "mallory", "password": "x"})
    disabled = login_client.post("/api/v1/auth/login", json={"username": "carol", "password": "x"})
    assert unknown.status_code == disabled.status_code == 401
    assert unknown.json() == disabled.json()
    reasons = sorted((row.detail or "").split()[1] for row in _failed_rows(session_factory))
    assert reasons == ["reason=disabled", "reason=unknown_user"]


def test_google_rejected_token_is_audited_and_throttled_per_ip(
    login_client: TestClient, session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        row = session.get(AuthSettings, 1)
        row.google_enabled = True
        row.google_client_id = "client.apps.googleusercontent.com"
        session.commit()
    monkeypatch.setattr(auth_routes, "verify_google_id_token", lambda *_a, **_k: None)
    statuses = [
        login_client.post("/api/v1/auth/google", json={"id_token": "bad"}).status_code
        for _ in range(6)
    ]
    assert statuses == [401] * 5 + [429]
    rows = _failed_rows(session_factory)
    assert len(rows) == 5
    assert all(row.origin == "google" and row.actor is None for row in rows)


def test_auth_audit_is_admin_only_and_hidden_from_fleet_audit(
    login_client: TestClient, session_factory: sessionmaker
) -> None:
    login_client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"})
    assert login_client.get("/api/v1/auth/audit", headers=VIEWER_HEADERS).status_code == 403
    admin = login_client.get("/api/v1/auth/audit", headers=ADMIN_HEADERS)
    assert admin.status_code == 200
    assert [item["actor"] for item in admin.json()] == ["alice"]
    with session_factory() as session:
        fleet = list_fleet_audit(session, limit=100)
    assert all(not item.action.startswith("auth.") for item in fleet.items)


@pytest.mark.parametrize(
    ("environment", "override", "served"),
    [("dev", None, True), ("prod", None, False), ("prod", True, True), ("staging", False, False)],
)
def test_api_docs_setting(environment: str, override: bool | None, served: bool) -> None:
    settings = Settings(_env_file=None, environment="test", api_docs_enabled=override)
    # environment is validated separately (prod needs real keys); only its value matters here.
    object.__setattr__(settings, "environment", environment)
    app = create_app(settings)
    client = TestClient(app)
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert (client.get(path).status_code == 200) is served, path


def test_metrics_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(Settings(_env_file=None, environment="test")))
    assert client.get("/metrics").status_code == 200
    with_token = Settings(
        _env_file=None, environment="test", metrics_bearer_token="scrape-token-0123456789"
    )
    monkeypatch.setattr(metrics_routes, "get_settings", lambda: with_token)
    assert client.get("/metrics").status_code == 401
    wrong = client.get("/metrics", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-token-0123456789"})
    assert ok.status_code == 200
    assert "cryo_auth_login_failures_total" in ok.text
    # The admin copy the UI reads is unaffected by the scrape token.
    assert client.get("/api/v1/ops/metrics", headers=ADMIN_HEADERS).status_code == 200
