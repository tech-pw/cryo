from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cryo.api.deps import get_tenant_session
from cryo.api.routes_column_usage import router
from cryo.api.schemas import TableStatsHistoryRead
from cryo.db.models import QueryPattern, Source, Table, TableStats, TableStatsHistory
from tests.auth_helpers import VIEWER_HEADERS, ensure_test_jwt_secret


def _client(session: Session) -> TestClient:
    ensure_test_jwt_secret()
    app = FastAPI()
    app.include_router(router)

    def override_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_tenant_session] = override_session
    return TestClient(app)


def _table(session: Session) -> Table:
    source = Source(id=uuid.uuid4(), name=f"s-{uuid.uuid4()}", type="rest", config={})
    table = Table(
        id=uuid.uuid4(), source_id=source.id, schema_name="a", table_name="t", is_active=True
    )
    session.add_all([source, table])
    session.commit()
    return table


def _pattern(table_id: uuid.UUID, column: str, seen: datetime, **counts: int) -> QueryPattern:
    return QueryPattern(
        table_id=table_id,
        column_name=column,
        filter_count=counts.get("filter", 0),
        equality_filter_count=counts.get("eq", 0),
        range_filter_count=counts.get("range", 0),
        join_count=counts.get("join", 0),
        projection_count=counts.get("proj", 0),
        last_seen_at=seen,
    )


def test_column_usage_keeps_only_the_latest_window_ranked_by_filters_and_joins(
    session: Session,
) -> None:
    table = _table(session)
    now = datetime(2026, 10, 18, 6, tzinfo=UTC)
    session.add_all(
        [
            TableStats(
                table_id=table.id,
                scan_at=now,
                needs_compaction=False,
                compaction_priority="LOW",
                grade="B",
                overall_score=80,
                total_files=10,
                small_files_count=0,
                total_size_gb=1.0,
                delete_file_pct=0.0,
                query_count_30d=200,
            ),
            _pattern(table.id, "event_date", now, filter=150, range=150, proj=10),
            _pattern(table.id, "user_id", now - timedelta(hours=1), filter=20, eq=20, join=60),
            _pattern(table.id, "payload", now, proj=180),
            _pattern(table.id, "legacy_col", now - timedelta(days=5), filter=999),
        ]
    )
    session.commit()

    body = (
        _client(session)
        .get(f"/api/v1/tables/{table.id}/column-usage", headers=VIEWER_HEADERS)
        .json()
    )

    assert body["queries"] == 200
    assert body["window_days"] == 30
    assert [column["column"] for column in body["columns"]] == ["event_date", "user_id", "payload"]
    assert body["columns"][1]["join_count"] == 60


def test_column_usage_is_empty_without_patterns(session: Session) -> None:
    table = _table(session)
    body = (
        _client(session)
        .get(f"/api/v1/tables/{table.id}/column-usage", headers=VIEWER_HEADERS)
        .json()
    )
    assert body["columns"] == [] and body["aggregated_at"] is None and body["queries"] is None


def test_history_rows_expose_delete_and_manifest_counts_from_raw(session: Session) -> None:
    table = _table(session)
    row = TableStatsHistory(
        table_id=table.id,
        scan_at=datetime(2026, 10, 18, tzinfo=UTC),
        total_files=100,
        raw={
            "total_delete_files": 12,
            "delete_file_pct": 10.7,
            "manifest_count": 40,
            "total_snapshots": 9,
        },
    )
    session.add(row)
    session.commit()
    read = TableStatsHistoryRead.model_validate(row)
    assert (read.total_delete_files, read.delete_file_pct) == (12, 10.7)
    assert (read.manifest_count, read.total_snapshots) == (40, 9)
    bare = TableStatsHistoryRead.model_validate(
        TableStatsHistory(id=2, table_id=table.id, scan_at=datetime(2026, 10, 1, tzinfo=UTC))
    )
    assert bare.total_delete_files is None
