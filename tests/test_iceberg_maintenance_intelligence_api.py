"""HTTP coverage for Iceberg maintenance-intelligence endpoints (RFC 2026-08-19)."""

from __future__ import annotations

from datetime import UTC, datetime

from cryo.db.models import QueryLog, QueryPattern
from tests.auth_helpers import VIEWER_HEADERS
from tests.test_partition_analyzer import _seed_stats, _seed_table
from tests.test_presets_api_db import ApiDb


def _seed_pattern_sqlite(
    session,
    table,
    *,
    column_name: str,
    filter_count: int,
    pattern_id: int,
    equality_filter_count: int = 0,
    avg_selectivity: float | None = None,
) -> None:
    session.add(
        QueryPattern(
            id=pattern_id,
            table_id=table.id,
            column_name=column_name,
            filter_count=filter_count,
            equality_filter_count=equality_filter_count,
            avg_selectivity=avg_selectivity,
        )
    )
    session.flush()


def _seed_query_logs_sqlite(session, table, *, n: int, bytes_read: int) -> None:
    now = datetime.now(UTC)
    for i in range(n):
        session.add(
            QueryLog(
                id=i + 1,
                source_id=table.source_id,
                table_id=table.id,
                query_id=f"q-api-{i}",
                engine="trino",
                executed_at=now,
                bytes_read=bytes_read,
            )
        )
    session.flush()


def test_partition_evolution_api_exposes_scan_ratio_trigger(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table = _seed_table(session, table_name="scan_ratio_api")
    total_size_gb = 10.0
    _seed_stats(
        session,
        table,
        partition_spec=[{"name": "event_date", "transform": "day"}],
        total_size_gb=total_size_gb,
    )
    _seed_pattern_sqlite(
        session,
        table,
        column_name="user_id",
        filter_count=40,
        equality_filter_count=40,
        avg_selectivity=0.4,
        pattern_id=1,
    )
    for i in range(9):
        _seed_pattern_sqlite(
            session, table, column_name=f"misc_{i}", filter_count=10, pattern_id=i + 2
        )
    table_bytes = int(total_size_gb * (1024**3))
    _seed_query_logs_sqlite(session, table, n=12, bytes_read=int(table_bytes * 0.20))
    table_id = table.id
    session.commit()
    session.close()

    response = api_db.client.get(
        f"/api/v1/advanced/partition-evolution/{table_id}",
        params={"min_query_threshold": 10},
        headers=VIEWER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trigger"] == "scan_ratio"
    assert body["scan_ratio"] > 0.10
    assert body["avg_selectivity"] == 0.4
    assert "user_id" in body["recommended_partition_spec"]


def test_fast_path_findings_api_wraps_list(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table = _seed_table(session, table_name="fast_path_api")
    table.first_seen_at = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    table_id = table.id
    session.commit()
    session.close()

    response = api_db.client.get(
        "/api/v1/advanced/fast-path-findings",
        headers=VIEWER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert "findings" in body
    types = {row["finding_type"] for row in body["findings"] if row["table_id"] == str(table_id)}
    assert "missing_target_file_size" in types
    assert all("table_name" in row for row in body["findings"])
