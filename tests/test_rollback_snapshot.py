"""Rollback moves the table's current pointer; it must stay admin-only and
require a fresh catalog read + explicit confirm before it ever commits."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cryo.api.deps import get_tenant_session
from cryo.api.extensions_routes.compliance import router
from cryo.api.snapshot_presence import CatalogMetadataError
from cryo.db.models import AuditLog, Source, Table
from tests.auth_helpers import (
    ADMIN_HEADERS,
    OPERATOR_HEADERS,
    VIEWER_HEADERS,
    ensure_test_jwt_secret,
)


def _client(session: Session) -> TestClient:
    ensure_test_jwt_secret()
    app = FastAPI()
    app.include_router(router)

    def override_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_tenant_session] = override_session
    return TestClient(app)


def _seed(session: Session) -> Table:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="rest", config={})
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="sales",
        table_name="orders",
        is_active=True,
    )
    session.add_all([source, table])
    session.commit()
    return table


class _FakeManageSnapshots:
    def __init__(self, calls: list[int]) -> None:
        self._calls = calls

    def rollback_to_snapshot(self, snapshot_id: int) -> _FakeManageSnapshots:
        self._calls.append(snapshot_id)
        return self

    def commit(self) -> None:
        return None


class _FakeMetadata:
    def __init__(self, current_snapshot_id: int) -> None:
        self.current_snapshot_id = current_snapshot_id


class _FakeIcebergTable:
    def __init__(self, current_snapshot_id: int, calls: list[int]) -> None:
        self.metadata = _FakeMetadata(current_snapshot_id)
        self._calls = calls

    def manage_snapshots(self) -> _FakeManageSnapshots:
        return _FakeManageSnapshots(self._calls)


class _FakeCatalog:
    def __init__(self, current_snapshot_id: int, calls: list[int]) -> None:
        self._current = current_snapshot_id
        self._calls = calls

    def load_table(self, _schema: str, _table: str) -> _FakeIcebergTable:
        return _FakeIcebergTable(self._current, self._calls)


def test_rollback_commits_to_the_requested_snapshot(session: Session, monkeypatch) -> None:
    table = _seed(session)
    calls: list[int] = []
    monkeypatch.setattr(
        "cryo.api.extensions_routes.compliance.load_current_snapshot_ids",
        lambda *_args, **_kwargs: {40, 41},
    )
    monkeypatch.setattr(
        "cryo.sources.factory.source_catalog_for",
        lambda *_args, **_kwargs: _FakeCatalog(current_snapshot_id=41, calls=calls),
    )
    response = _client(session).post(
        f"/tables/{table.id}/snapshots/40/rollback",
        headers=ADMIN_HEADERS,
        json={"snapshot_id": "40", "confirm": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == "40"
    assert body["previous_snapshot_id"] == "41"
    assert calls == [40]

    audit = session.query(AuditLog).filter(AuditLog.table_id == table.id).one()
    assert audit.action == "snapshot.rollback"
    assert "snapshot_id=40" in audit.detail
    assert "previous_snapshot_id=41" in audit.detail


def test_rollback_requires_confirm(session: Session, monkeypatch) -> None:
    table = _seed(session)
    monkeypatch.setattr(
        "cryo.api.extensions_routes.compliance.load_current_snapshot_ids",
        lambda *_args, **_kwargs: {40},
    )
    response = _client(session).post(
        f"/tables/{table.id}/snapshots/40/rollback",
        headers=ADMIN_HEADERS,
        json={"snapshot_id": "40", "confirm": False},
    )
    assert response.status_code == 400


def test_rollback_404s_when_snapshot_is_not_in_current_metadata(
    session: Session, monkeypatch
) -> None:
    table = _seed(session)
    monkeypatch.setattr(
        "cryo.api.extensions_routes.compliance.load_current_snapshot_ids",
        lambda *_args, **_kwargs: {41},
    )
    response = _client(session).post(
        f"/tables/{table.id}/snapshots/40/rollback",
        headers=ADMIN_HEADERS,
        json={"snapshot_id": "40", "confirm": True},
    )
    assert response.status_code == 404


def test_rollback_503s_when_metadata_cannot_be_read(session: Session, monkeypatch) -> None:
    table = _seed(session)

    def _fail(*_args, **_kwargs):
        raise CatalogMetadataError("metastore down")

    monkeypatch.setattr("cryo.api.extensions_routes.compliance.load_current_snapshot_ids", _fail)
    response = _client(session).post(
        f"/tables/{table.id}/snapshots/40/rollback",
        headers=ADMIN_HEADERS,
        json={"snapshot_id": "40", "confirm": True},
    )
    assert response.status_code == 503


def test_operator_cannot_rollback(session: Session, monkeypatch) -> None:
    table = _seed(session)
    monkeypatch.setattr(
        "cryo.api.extensions_routes.compliance.load_current_snapshot_ids",
        lambda *_args, **_kwargs: {40},
    )
    response = _client(session).post(
        f"/tables/{table.id}/snapshots/40/rollback",
        headers=OPERATOR_HEADERS,
        json={"snapshot_id": "40", "confirm": True},
    )
    assert response.status_code == 403


def test_viewer_cannot_rollback(session: Session, monkeypatch) -> None:
    table = _seed(session)
    monkeypatch.setattr(
        "cryo.api.extensions_routes.compliance.load_current_snapshot_ids",
        lambda *_args, **_kwargs: {40},
    )
    response = _client(session).post(
        f"/tables/{table.id}/snapshots/40/rollback",
        headers=VIEWER_HEADERS,
        json={"snapshot_id": "40", "confirm": True},
    )
    assert response.status_code == 403
