"""Protect must not store a snapshot id that is not in current metadata."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.api.deps import get_tenant_session
from cryo.api.extensions_routes.compliance import router
from cryo.api.snapshot_presence import CatalogMetadataError, collect_snapshot_ids
from cryo.db.models import ProtectedSnapshot, Source, Table
from tests.auth_helpers import OPERATOR_HEADERS, VIEWER_HEADERS, ensure_test_jwt_secret

BIG = 2**53 + 3


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


def _count(session: Session, table_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(ProtectedSnapshot).where(ProtectedSnapshot.table_id == table_id)
        )
        or 0
    )


class _Snap:
    def __init__(self, snapshot_id: int, calls: dict[str, int]) -> None:
        self.snapshot_id = snapshot_id
        self._calls = calls

    def manifests(self, io: object = None) -> list[object]:
        self._calls["manifests"] += 1
        raise AssertionError("must not list manifests")


def test_collect_snapshot_ids_keeps_values_above_2_53_and_does_not_list_manifests() -> None:
    calls = {"manifests": 0}
    metadata = type("Meta", (), {"snapshots": [_Snap(BIG, calls)]})()
    assert collect_snapshot_ids(metadata) == {BIG}
    assert calls["manifests"] == 0


def test_protect_missing_snapshot_is_404_and_stores_nothing(session: Session, monkeypatch) -> None:
    table = _seed(session)
    monkeypatch.setattr(
        "cryo.api.extensions_routes.compliance.load_current_snapshot_ids",
        lambda *_args, **_kwargs: {1},
    )
    response = _client(session).post(
        f"/tables/{table.id}/protected-snapshots",
        headers=OPERATOR_HEADERS,
        json={"snapshot_id": str(BIG)},
    )
    assert response.status_code == 404
    assert _count(session, table.id) == 0
    session.expire_all()
    assert _count(session, table.id) == 0


def test_protect_exact_id_above_2_53_round_trips_as_a_decimal_string(session: Session, monkeypatch) -> None:
    table = _seed(session)
    monkeypatch.setattr(
        "cryo.api.extensions_routes.compliance.load_current_snapshot_ids",
        lambda *_args, **_kwargs: {BIG},
    )
    response = _client(session).post(
        f"/tables/{table.id}/protected-snapshots",
        headers=OPERATOR_HEADERS,
        json={"snapshot_id": str(BIG), "label": "keep"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["snapshot_id"] == str(BIG)
    assert isinstance(body["snapshot_id"], str)
    session.expire_all()
    stored = session.scalar(select(ProtectedSnapshot).where(ProtectedSnapshot.table_id == table.id))
    assert stored is not None
    assert stored.snapshot_id == BIG


def test_protect_does_not_store_a_row_when_metadata_cannot_be_read(session: Session, monkeypatch) -> None:
    table = _seed(session)

    def _fail(*_args, **_kwargs):
        raise CatalogMetadataError("metastore down")

    monkeypatch.setattr("cryo.api.extensions_routes.compliance.load_current_snapshot_ids", _fail)
    response = _client(session).post(
        f"/tables/{table.id}/protected-snapshots",
        headers=OPERATOR_HEADERS,
        json={"snapshot_id": str(BIG)},
    )
    assert response.status_code == 503
    assert _count(session, table.id) == 0


def test_viewer_cannot_protect(session: Session, monkeypatch) -> None:
    table = _seed(session)
    monkeypatch.setattr(
        "cryo.api.extensions_routes.compliance.load_current_snapshot_ids",
        lambda *_args, **_kwargs: {BIG},
    )
    response = _client(session).post(
        f"/tables/{table.id}/protected-snapshots",
        headers=VIEWER_HEADERS,
        json={"snapshot_id": str(BIG)},
    )
    assert response.status_code == 403
    assert _count(session, table.id) == 0
