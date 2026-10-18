"""The compliance summary behind the table settings drawer."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cryo.api.deps import get_tenant_session
from cryo.api.extensions_routes.compliance import router
from cryo.db.models import DeletionTracking, ProtectedSnapshot, Source, Table
from tests.auth_helpers import VIEWER_HEADERS, ensure_test_jwt_secret


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
    table = Table(id=uuid.uuid4(), source_id=source.id, schema_name="sales", table_name="orders")
    session.add_all([source, table])
    session.commit()
    return table


def test_tracked_deletions_report_the_real_date_and_protected_snapshots_count(
    session: Session,
) -> None:
    table = _seed(session)
    until = datetime(2026, 11, 1, tzinfo=UTC)
    session.add_all(
        [
            DeletionTracking(
                id=uuid.uuid4(),
                table_id=table.id,
                subject_id="user-1",
                soft_deleted_at=until - timedelta(days=7),
                pending_rows=40,
                blocked_until=until - timedelta(days=1),
                reason="GDPR erasure",
            ),
            DeletionTracking(
                id=uuid.uuid4(),
                table_id=table.id,
                subject_id="user-2",
                soft_deleted_at=until - timedelta(days=6),
                pending_rows=2,
                blocked_until=until,
                reason="GDPR erasure",
            ),
            ProtectedSnapshot(id=uuid.uuid4(), table_id=table.id, snapshot_id=7, label="audit"),
        ]
    )
    session.commit()
    body = _client(session).get(f"/tables/{table.id}/compliance", headers=VIEWER_HEADERS).json()
    assert body["pending_deletion"]["pending_rows"] == 42
    assert body["pending_deletion"]["blocked_until"].startswith("2026-11-01")
    assert body["protected_snapshot_count"] == 1
    assert body["abac_enforced"] is False


def test_nothing_pending_is_null(session: Session) -> None:
    table = _seed(session)
    body = _client(session).get(f"/tables/{table.id}/compliance", headers=VIEWER_HEADERS).json()
    assert body["pending_deletion"] is None
    assert body["protected_snapshot_count"] == 0
