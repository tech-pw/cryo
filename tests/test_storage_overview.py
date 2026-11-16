"""Home storage overview: breakdown, last-24h freed, cumulative and 60-day trend."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cryo.api.deps import get_tenant_session
from cryo.api.routes_impact import router
from cryo.db.models import AuditLog, FleetImpactHistory, Source, Table, TableCost
from cryo.reporting.storage_overview import (
    TIB,
    fleet_storage_breakdown,
    freed_last_24h,
    storage_overview,
    storage_snapshot_for_history,
    storage_trend,
)
from tests.auth_helpers import VIEWER_HEADERS, ensure_test_jwt_secret

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
GIB = 1024**3


def _table(session: Session, *, active: bool = True) -> Table:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="s",
        table_name=f"t{uuid4().hex[:6]}",
        is_active=active,
    )
    session.add_all([source, table])
    session.flush()
    return table


def _cost(
    session: Session,
    table: Table,
    *,
    live: int,
    dead: int = 0,
    meta: int = 0,
    orphan: int | None = None,
    listed: int | None = None,
    raw: dict[str, Any] | None = None,
) -> None:
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=NOW,
            precision="metadata",
            price_per_gb_month=0.02,
            live_bytes=live,
            dead_bytes=dead,
            metadata_bytes=meta,
            orphan_bytes=orphan,
            listed_bytes=listed,
            reclaim_optimize_bytes=GIB,
            current_monthly_cost_usd=1.0,
            projected_monthly_cost_usd=1.0,
            projected_savings_usd_month=0.0,
            raw=raw,
        )
    )
    session.flush()


def _client(session: Session) -> TestClient:
    ensure_test_jwt_secret()
    app = FastAPI()
    app.include_router(router)

    def override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_tenant_session] = override
    return TestClient(app)


def test_breakdown_splits_listed_history_and_counts_coverage(session: Session) -> None:
    listed_table = _table(session)
    _cost(
        session,
        listed_table,
        live=100 * GIB,
        dead=20 * GIB,
        meta=GIB,
        orphan=9 * GIB,
        listed=160 * GIB,
    )
    unlisted = _table(session)
    _cost(session, unlisted, live=50 * GIB, dead=5 * GIB, meta=GIB, orphan=7 * GIB)
    _table(session)
    inactive = _table(session, active=False)
    _cost(session, inactive, live=999 * GIB)

    b = fleet_storage_breakdown(session)
    assert b.tables_total == 3
    assert b.tables_with_cost == 2
    assert b.tables_with_listing == 1
    assert b.live_bytes == 150 * GIB
    assert b.dead_bytes == 25 * GIB
    # 160 listed - (100 + 20 + 1 + 9) accounted = 30 retained, only for the listed table.
    assert b.retained_history_bytes == 30 * GIB
    # Orphans only count where a listing confirmed them.
    assert b.orphan_bytes == 9 * GIB
    assert b.compaction_savings_bytes == 2 * GIB
    assert b.total_bytes == (150 + 30 + 25 + 9 + 2) * GIB
    assert b.price_per_gb_month == 0.02


def test_freed_24h_counts_only_successful_reclaim_in_window(session: Session) -> None:
    t1, t2 = _table(session), _table(session)
    rows: list[tuple[Table, str, str, int | None, int]] = [
        (t1, "remove_orphan_files", "OK", 4 * GIB, 1),
        (t2, "optimize", "succeeded", None, 2),
        (t1, "optimize", "failed", 99 * GIB, 3),
        (t1, "remove_orphan_files", "OK", 50 * GIB, 30),
    ]
    for table, action, status, freed, hours in rows:
        session.add(
            AuditLog(
                ts=NOW - timedelta(hours=hours),
                table_id=table.id,
                action=action,
                status=status,
                actual_bytes_freed=freed,
            )
        )
    session.flush()
    freed_24h = freed_last_24h(session, now=NOW)
    assert freed_24h.bytes_freed == 4 * GIB
    assert freed_24h.actions_succeeded == 2
    assert freed_24h.actions_measured == 1
    assert freed_24h.tables_cleaned == 2


def _history(
    session: Session,
    day: date,
    *,
    freed: int,
    state: str = "complete",
    total_tb: float | None = 2.0,
    storage: dict[str, Any] | None = None,
) -> None:
    session.add(
        FleetImpactHistory(
            report_date=day,
            recorded_at=NOW,
            bytes_freed=freed,
            measurement_state=state,
            total_size_tb=total_tb,
            raw={"storage": storage} if storage else {},
        )
    )


def test_trend_backfills_total_and_skips_unmeasured_in_cumulative(session: Session) -> None:
    today = NOW.date()
    _history(session, today - timedelta(days=100), freed=10 * GIB)
    _history(session, today - timedelta(days=2), freed=GIB)
    _history(session, today - timedelta(days=1), freed=0, state="unmeasured")
    _history(session, today, freed=2 * GIB, storage={"total_bytes": 3 * TIB, "live_bytes": 2 * TIB})
    session.flush()

    points = storage_trend(session, today=today)
    assert [p.day for p in points] == [today - timedelta(days=d) for d in (2, 1, 0)]
    assert points[0].total_bytes == 2 * TIB
    assert points[0].live_bytes is None
    assert points[1].bytes_freed is None
    assert [p.cumulative_freed_bytes for p in points] == [11 * GIB, 11 * GIB, 13 * GIB]
    assert points[2].live_bytes == 2 * TIB

    overview = storage_overview(session, now=NOW)
    assert overview.cumulative.bytes_freed == 13 * GIB
    assert overview.cumulative.days_unmeasured == 1
    assert overview.cumulative.since == today - timedelta(days=100)


def test_versioning_is_none_until_reported_then_flags_missing_rule(session: Session) -> None:
    _cost(session, _table(session), live=GIB, listed=GIB, orphan=0)
    assert storage_overview(session, now=NOW).versioning_findings is None

    for bucket, rule in (("b1", False), ("b2", True)):
        _cost(
            session,
            _table(session),
            live=GIB,
            listed=GIB,
            orphan=0,
            raw={
                "bucket_versioning": {
                    "bucket": bucket,
                    "status": "Enabled",
                    "noncurrent_expiration": rule,
                }
            },
        )
    findings = storage_overview(session, now=NOW).versioning_findings
    assert findings is not None
    assert [(f.bucket, f.tables) for f in findings] == [("b1", 1)]


def test_snapshot_for_history_returns_breakdown(session: Session) -> None:
    _cost(session, _table(session), live=GIB)
    snapshot = storage_snapshot_for_history(session)
    assert snapshot is not None
    assert snapshot["live_bytes"] == GIB
    assert snapshot["total_bytes"] == GIB


def test_api_requires_auth_and_returns_overview(session: Session) -> None:
    _cost(session, _table(session), live=GIB, listed=GIB, orphan=0)
    session.add(
        AuditLog(
            ts=datetime.now(UTC) - timedelta(hours=1),
            table_id=None,
            action="remove_orphan_files",
            status="OK",
            actual_bytes_freed=GIB,
        )
    )
    session.flush()
    client = _client(session)
    assert client.get("/api/v1/impact/storage-overview").status_code == 401
    response = client.get("/api/v1/impact/storage-overview", headers=VIEWER_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["breakdown"]["tables_with_listing"] == 1
    assert body["freed_24h"]["bytes_freed"] == GIB
    assert body["freed_24h"]["savings_usd_month"] == 0.02
    assert body["versioning_findings"] is None
    assert body["trend"] == []


def test_freed_24h_groups_by_bytes_source_when_tagged(session: Session) -> None:
    table = _table(session)
    for source, freed in (("engine", 3 * GIB), ("scan_estimate", GIB), (None, 5 * GIB)):
        session.add(
            AuditLog(
                ts=NOW - timedelta(hours=1),
                table_id=table.id,
                action="remove_orphan_files",
                status="OK",
                actual_bytes_freed=freed,
                result_metrics={"bytes_source": source} if source else {},
            )
        )
    session.flush()
    freed_24h = freed_last_24h(session, now=NOW)
    assert freed_24h.bytes_by_source == {"engine": 3 * GIB, "scan_estimate": GIB}
    assert freed_24h.bytes_freed == 9 * GIB


def test_freed_24h_source_is_none_without_tags(session: Session) -> None:
    session.add(
        AuditLog(
            ts=NOW - timedelta(hours=1),
            table_id=_table(session).id,
            action="optimize",
            status="OK",
            actual_bytes_freed=GIB,
        )
    )
    session.flush()
    assert freed_last_24h(session, now=NOW).bytes_by_source is None
