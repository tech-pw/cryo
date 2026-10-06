"""GET /api/v1/tables/{id}/snapshots — metadata only, no file listing."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.api.deps import get_tenant_session
from cryo.api.routes_snapshots import router
from cryo.db.models import ProtectedSnapshot, Source, Table, TableStatsHistory
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


class _Snap:
    def __init__(
        self,
        snapshot_id: int,
        timestamp_ms: int,
        sequence_number: int | None,
        summary: dict[str, str] | None,
        calls: dict[str, int],
    ) -> None:
        self.snapshot_id = snapshot_id
        self.timestamp_ms = timestamp_ms
        self.sequence_number = sequence_number
        self.summary = summary
        self._calls = calls

    def manifests(self, io: object = None) -> list[object]:
        self._calls["manifests"] += 1
        raise AssertionError("must not list manifests")


class _Ref:
    def __init__(self, snapshot_id: int, kind: str, **retention: int | None) -> None:
        self.snapshot_id = snapshot_id
        self.snapshot_ref_type = kind
        self.min_snapshots_to_keep = retention.get("min_snapshots_to_keep")
        self.max_snapshot_age_ms = retention.get("max_snapshot_age_ms")
        self.max_ref_age_ms = retention.get("max_ref_age_ms")


class _Metadata:
    def __init__(self, current: int | None, snapshots: list[Any], refs: dict[str, Any]) -> None:
        self.current_snapshot_id = current
        self.snapshots = snapshots
        self.refs = refs


class _Iceberg:
    def __init__(self, metadata: _Metadata, calls: dict[str, int]) -> None:
        self.metadata = metadata
        self._calls = calls

    def scan(self) -> None:
        self._calls["scan"] += 1
        raise AssertionError("must not scan data files")

    def inspect(self) -> None:
        self._calls["inspect"] += 1
        raise AssertionError("must not inspect files")


def _catalog(metadata: _Metadata, calls: dict[str, int]) -> Any:
    class _Catalog:
        def load_table(self, schema: str, table: str) -> _Iceberg:
            return _Iceberg(metadata, calls)

    return _Catalog()


def _history_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(TableStatsHistory)) or 0)


def test_pages_refs_missing_summary_and_protected_without_listing_files(
    session: Session, monkeypatch: Any
) -> None:
    table = _seed(session)
    session.add(
        ProtectedSnapshot(
            id=uuid.uuid4(),
            table_id=table.id,
            snapshot_id=101,
            label="year-end",
            protected_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    session.commit()
    calls = {"manifests": 0, "scan": 0, "inspect": 0}
    snaps = [
        _Snap(101, 1_700_000_001_000, 1, {"operation": "append", "total-records": "3"}, calls),
        _Snap(102, 1_700_000_002_000, 2, {"operation": "append"}, calls),
        _Snap(
            103,
            1_700_000_003_000,
            3,
            {"operation": "replace", "total-records": "9", "total-files-size": "0"},
            calls,
        ),
    ]
    metadata = _Metadata(
        103,
        snaps,
        {
            "main": _Ref(103, "branch", min_snapshots_to_keep=4),
            "year-end": _Ref(101, "tag", max_ref_age_ms=604_800_000),
        },
    )
    monkeypatch.setattr(
        "cryo.sources.factory.source_catalog_for", lambda source: _catalog(metadata, calls)
    )
    client = _client(session)

    first = client.get(
        f"/api/v1/tables/{table.id}/snapshots",
        params={"page": 1, "page_size": 2},
        headers=VIEWER_HEADERS,
    )
    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["pinned_count"] == 2
    assert body["current_snapshot_id"] == "103"
    assert [row["snapshot_id"] for row in body["snapshots"]] == ["103", "102"]
    assert body["snapshots"][0]["parent_snapshot_id"] is None
    assert body["snapshots"][1]["parent_snapshot_id"] is None

    current = body["snapshots"][0]
    assert current["is_current"] is True
    assert current["operation"] == "replace"
    assert current["summary"]["total_records"] == 9
    assert current["summary"]["total_files_size"] == 0
    assert current["summary"]["added_data_files"] is None
    assert current["summary"]["deleted_data_files"] is None
    assert current["refs"] == [
        {
            "name": "main",
            "type": "branch",
            "snapshot_id": "103",
            "min_snapshots_to_keep": 4,
            "max_snapshot_age_ms": None,
            "max_ref_age_ms": None,
        }
    ]
    assert current["protected"] is False

    missing = body["snapshots"][1]
    assert missing["summary"] == {
        "total_records": None,
        "total_files_size": None,
        "added_data_files": None,
        "deleted_data_files": None,
    }
    assert missing["protected"] is False

    second = client.get(
        f"/api/v1/tables/{table.id}/snapshots",
        params={"page": 2, "page_size": 2},
        headers=VIEWER_HEADERS,
    )
    assert second.status_code == 200
    older = second.json()["snapshots"]
    assert [row["snapshot_id"] for row in older] == ["101"]
    assert older[0]["parent_snapshot_id"] is None
    assert older[0]["protected"] is True
    assert older[0]["protected_label"] == "year-end"
    assert older[0]["refs"] == [
        {
            "name": "year-end",
            "type": "tag",
            "snapshot_id": "101",
            "min_snapshots_to_keep": None,
            "max_snapshot_age_ms": None,
            "max_ref_age_ms": 604_800_000,
        }
    ]
    assert calls == {"manifests": 0, "scan": 0, "inspect": 0}
    assert _history_count(session) == 0


def test_catalog_timeout_returns_503(session: Session, monkeypatch: Any) -> None:
    table = _seed(session)

    class _Hanging:
        def load_table(self, schema: str, table_name: str) -> None:
            time.sleep(3)
            raise AssertionError("should have timed out")

    monkeypatch.setattr("cryo.sources.factory.source_catalog_for", lambda source: _Hanging())
    monkeypatch.setattr("cryo.api.routes_snapshots._SNAPSHOTS_CATALOG_TIMEOUT_SECONDS", 0.2)
    client = _client(session)

    started = time.monotonic()
    response = client.get(f"/api/v1/tables/{table.id}/snapshots", headers=VIEWER_HEADERS)
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert "timed out" in response.json()["detail"]
    assert elapsed < 2.0
    assert _history_count(session) == 0


def test_page_size_over_100_is_rejected(session: Session) -> None:
    table = _seed(session)
    client = _client(session)
    response = client.get(
        f"/api/v1/tables/{table.id}/snapshots",
        params={"page_size": 101},
        headers=VIEWER_HEADERS,
    )
    assert response.status_code == 422


def test_large_ids_are_decimal_strings_and_parent_is_not_inferred(
    session: Session, monkeypatch: Any
) -> None:
    from pyiceberg.table.refs import SnapshotRef
    from pyiceberg.table.snapshots import Snapshot

    table = _seed(session)
    big = 9223372036854775807
    parent = 9223372036854775806
    session.add(
        ProtectedSnapshot(
            id=uuid.uuid4(),
            table_id=table.id,
            snapshot_id=big,
            label="keep",
            protected_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    session.commit()
    calls = {"manifests": 0, "scan": 0, "inspect": 0}
    current = Snapshot(
        snapshot_id=big,
        parent_snapshot_id=parent,
        timestamp_ms=1_700_000_002_000,
        sequence_number=2,
        manifest_list="s3://bucket/current.avro",
        summary={"operation": "append"},
    )
    assert current.parent_snapshot_id == parent
    adjacent = {
        "snapshot-id": 100,
        "timestamp_ms": 1_700_000_001_000,
        "sequence_number": 1,
        "summary": {"operation": "append"},
    }
    metadata = _Metadata(
        big,
        [current, adjacent],
        {"main": SnapshotRef(snapshot_id=big, type="branch", min_snapshots_to_keep=1)},
    )
    monkeypatch.setattr(
        "cryo.sources.factory.source_catalog_for", lambda source: _catalog(metadata, calls)
    )
    client = _client(session)

    response = client.get(f"/api/v1/tables/{table.id}/snapshots", headers=VIEWER_HEADERS)
    assert response.status_code == 200
    raw = response.text
    assert '"9223372036854775807"' in raw
    assert '"9223372036854775806"' in raw
    assert re.search(r'(?<!")9223372036854775807(?!")', raw) is None
    assert re.search(r'(?<!")9223372036854775806(?!")', raw) is None

    body = response.json()
    assert isinstance(body["current_snapshot_id"], str)
    assert body["current_snapshot_id"] == "9223372036854775807"
    rows = body["snapshots"]
    assert [row["snapshot_id"] for row in rows] == ["9223372036854775807", "100"]
    assert all(isinstance(row["snapshot_id"], str) for row in rows)
    assert rows[0]["parent_snapshot_id"] == "9223372036854775806"
    assert isinstance(rows[0]["parent_snapshot_id"], str)
    assert rows[0]["parent_snapshot_id"] != rows[1]["snapshot_id"]
    assert rows[1]["parent_snapshot_id"] is None
    assert rows[0]["protected"] is True
    assert rows[0]["protected_label"] == "keep"
    assert rows[0]["refs"][0]["snapshot_id"] == "9223372036854775807"
    assert isinstance(rows[0]["refs"][0]["snapshot_id"], str)
    assert rows[0]["summary"]["added_data_files"] is None
    assert calls == {"manifests": 0, "scan": 0, "inspect": 0}
    assert _history_count(session) == 0


def test_missing_table_is_404(session: Session) -> None:
    client = _client(session)
    response = client.get(f"/api/v1/tables/{uuid.uuid4()}/snapshots", headers=VIEWER_HEADERS)
    assert response.status_code == 404
