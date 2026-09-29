from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.api.deps import get_tenant_session
from cryo.api.routes_batch import router
from cryo.db.models import MaintenanceJob, MaintenanceRun, Source, Table
from cryo.maintenance.batch_selection import (
    DEFAULT_BATCH_CAP,
    BatchSelectionCapExceeded,
    BatchSelectionExpired,
    ReceiptStore,
    default_store,
    resolve_batch_selection,
)
from tests.auth_helpers import OPERATOR_HEADERS, VIEWER_HEADERS, ensure_test_jwt_secret

NIL = uuid.UUID(int=0)
BRONZE = {"field": "schema_name", "op": "=", "value": "bronze"}


@pytest.fixture(autouse=True)
def _clear_receipts() -> Iterator[None]:
    default_store.clear()
    yield
    default_store.clear()


def _seed(
    session: Session,
    names: list[str],
    *,
    schema_name: str = "bronze",
    catalog: str = "lake",
) -> list[Table]:
    source = Source(
        id=uuid.uuid4(),
        name=f"src-{uuid.uuid4()}",
        type="rest",
        config={"catalog_name": catalog},
    )
    session.add(source)
    session.flush()
    tables = [
        Table(
            id=uuid.uuid4(),
            source_id=source.id,
            schema_name=schema_name,
            table_name=name,
            is_active=True,
        )
        for name in names
    ]
    session.add_all(tables)
    session.commit()
    return tables


def _resolve(session: Session, store: ReceiptStore, **kwargs: object):
    return resolve_batch_selection(
        session,
        principal="operator",
        query_tenant_id=NIL,
        store=store,
        **kwargs,
    )


def _client(session: Session) -> TestClient:
    ensure_test_jwt_secret()
    app = FastAPI()
    app.include_router(router)

    def override_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_tenant_session] = override_session
    return TestClient(app)


def test_dedupes_by_uuid(session: Session) -> None:
    tables = _seed(session, ["a", "b"])
    store = ReceiptStore()
    first = _resolve(session, store, table_ids=[tables[0].id])
    second = _resolve(
        session,
        store,
        table_ids=[tables[0].id, tables[0].id, tables[1].id],
        receipt_ids=[first.receipt_id],
    )
    assert second.unique_count == 2
    assert [row.id for row in second.tables].count(tables[0].id) == 1
    assert {row.id for row in second.tables} == {tables[0].id, tables[1].id}
    row = next(item for item in second.tables if item.id == tables[0].id)
    assert row.catalog == "lake"
    assert row.schema == "bronze"
    assert row.name == "a"
    assert second.overflow is False
    assert session.scalar(select(func.count()).select_from(MaintenanceRun)) == 0
    assert session.scalar(select(func.count()).select_from(MaintenanceJob)) == 0


def test_exclusion_removes_filter_and_receipt_matches(session: Session) -> None:
    tables = _seed(session, ["a", "b", "c"])
    other = _seed(session, ["d"], schema_name="silver")
    store = ReceiptStore()
    held = _resolve(session, store, table_ids=[tables[1].id])
    receipt = _resolve(
        session,
        store,
        filter_spec=BRONZE,
        receipt_ids=[held.receipt_id],
        exclude_ids=[tables[1].id],
    )
    found = {row.id for row in receipt.tables}
    assert receipt.unique_count == 2
    assert tables[1].id not in found
    assert other[0].id not in found
    assert found == {tables[0].id, tables[2].id}


def test_cap_rejects_addition_without_truncating(session: Session) -> None:
    tables = _seed(session, ["a", "b", "c"])
    store = ReceiptStore()
    held = _resolve(session, store, table_ids=[tables[0].id, tables[1].id], cap=2)
    assert held.unique_count == 2
    assert held.overflow is False
    with pytest.raises(BatchSelectionCapExceeded) as caught:
        _resolve(
            session,
            store,
            table_ids=[tables[2].id],
            receipt_ids=[held.receipt_id],
            cap=2,
        )
    assert "2-table batch limit" in str(caught.value)
    assert caught.value.overflow is True
    assert len(store) == 1
    with pytest.raises(BatchSelectionCapExceeded):
        _resolve(session, store, filter_spec=BRONZE, cap=2)
    assert len(store) == 1
    narrowed = _resolve(
        session,
        store,
        filter_spec=BRONZE,
        exclude_ids=[tables[2].id],
        cap=2,
    )
    assert narrowed.unique_count == 2
    assert tables[2].id not in {row.id for row in narrowed.tables}


def test_default_cap_message_names_100(session: Session) -> None:
    assert DEFAULT_BATCH_CAP == 100
    with pytest.raises(BatchSelectionCapExceeded) as caught:
        _resolve(session, ReceiptStore(), table_ids=[uuid.uuid4() for _ in range(101)])
    assert "100-table batch limit" in str(caught.value)


def test_filter_includes_every_match(session: Session) -> None:
    tables = _seed(session, ["a", "b", "c", "d", "e"])
    receipt = _resolve(session, ReceiptStore(), filter_spec=BRONZE)
    assert receipt.unique_count == 5
    assert {row.id for row in receipt.tables} == {table.id for table in tables}


def test_view_filter_matches_list_tables(session: Session) -> None:
    _seed(session, ["a"])
    receipt = _resolve(session, ReceiptStore(), freshness_breach=True, match_filter=True)
    assert receipt.unique_count == 0
    assert receipt.overflow is False


def test_expired_receipt_does_not_expand(session: Session) -> None:
    from datetime import UTC, datetime, timedelta

    tables = _seed(session, ["a", "b"])
    store = ReceiptStore()
    created = datetime(2026, 9, 29, tzinfo=UTC)
    first = _resolve(session, store, table_ids=[tables[0].id], now=created)
    with pytest.raises(BatchSelectionExpired):
        _resolve(
            session,
            store,
            table_ids=[tables[1].id],
            receipt_ids=[first.receipt_id],
            now=created + timedelta(minutes=11),
        )
    assert len(store) == 1


def test_http_resolve_cap_roles_and_read(session: Session) -> None:
    tables = _seed(session, ["orders"], schema_name="sales", catalog="iceberg")
    client = _client(session)
    denied = client.post(
        "/api/v1/maintenance/batch/resolve",
        json={"table_ids": [str(tables[0].id)]},
        headers=VIEWER_HEADERS,
    )
    assert denied.status_code == 403

    created = client.post(
        "/api/v1/maintenance/batch/resolve",
        json={"table_ids": [str(tables[0].id)]},
        headers=OPERATOR_HEADERS,
    )
    assert created.status_code == 200
    body = created.json()
    assert body["overflow"] is False
    assert body["unique_count"] == 1
    assert body["tables"] == [
        {
            "id": str(tables[0].id),
            "catalog": "iceberg",
            "schema": "sales",
            "name": "orders",
        }
    ]
    read = client.get(
        f"/api/v1/maintenance/batch/receipts/{body['receipt_id']}",
        headers=VIEWER_HEADERS,
    )
    assert read.status_code == 200
    assert read.json()["tables"] == body["tables"]

    over = client.post(
        "/api/v1/maintenance/batch/resolve",
        json={"table_ids": [str(uuid.uuid4()) for _ in range(101)]},
        headers=OPERATOR_HEADERS,
    )
    assert over.status_code == 409
    detail = over.json()["detail"]
    assert detail["overflow"] is True
    assert "100-table batch limit" in detail["message"]
    assert "receipt_id" not in over.json()
