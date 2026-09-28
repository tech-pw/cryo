"""GET /api/v1/impact/history — viewer read of the daily impact ledger.

An unmeasured day is stored with bytes_freed=0. The API must return that day
with measurement_state='unmeasured', not as a zero-byte complete day.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cryo.api.deps import get_tenant_session
from cryo.api.routes_impact import router
from cryo.db.models import AuditLog, Source, Table
from cryo.reporting.impact_history import record_daily_impact
from cryo.reporting.impact_signals import compute_impact_signal, day_bounds
from tests.auth_helpers import VIEWER_HEADERS, ensure_test_jwt_secret

NOW = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)


def _seed_source_table(session: Session) -> tuple[Source, Table]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="s",
        table_name="t",
        is_active=True,
    )
    session.add_all([source, table])
    session.flush()
    return source, table


def _log(
    session: Session,
    table: Table,
    *,
    days_ago: int,
    bytes_freed: int | None,
    engine: str | None = None,
) -> None:
    day_start, _ = day_bounds(NOW, days_ago)
    session.add(
        AuditLog(
            ts=day_start + timedelta(hours=6),
            table_id=table.id,
            action="remove_orphan_files",
            status="OK",
            engine=engine,
            actual_bytes_freed=bytes_freed,
        )
    )


@dataclass
class _FakeReportData:
    impact_signal: object
    total_tables: int = 100
    unhealthy_tables: int = 10
    total_size_tb: float = 5.0
    total_cost_usd: float = 200.0
    projected_savings_usd: float = 50.0


def _record_day(
    session: Session,
    table: Table,
    *,
    days_ago: int,
    bytes_freed: int | None,
    engine: str | None = None,
) -> None:
    _log(session, table, days_ago=days_ago, bytes_freed=bytes_freed, engine=engine)
    session.commit()
    now = NOW - timedelta(days=days_ago - 1)
    signal = compute_impact_signal(session, now=now)
    record_daily_impact(session, report_data=_FakeReportData(impact_signal=signal), now=now)
    session.commit()


def _client(session: Session) -> TestClient:
    ensure_test_jwt_secret()
    app = FastAPI()
    app.include_router(router)

    def override_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_tenant_session] = override_session
    return TestClient(app)


def test_unmeasured_day_is_returned_with_its_state_not_as_a_complete_zero(session: Session) -> None:
    _source, table = _seed_source_table(session)
    _record_day(session, table, days_ago=2, bytes_freed=5 * 1024**3, engine="native")
    _record_day(session, table, days_ago=1, bytes_freed=None)

    measured_date = day_bounds(NOW, 2)[0].date().isoformat()
    unmeasured_date = day_bounds(NOW, 1)[0].date().isoformat()
    client = _client(session)
    response = client.get(
        "/api/v1/impact/history",
        params={"since": measured_date, "until": unmeasured_date},
        headers=VIEWER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    by_date = {row["report_date"]: row for row in body["rows"]}

    unmeasured = by_date[unmeasured_date]
    assert unmeasured["measurement_state"] == "unmeasured"
    assert unmeasured["measurement_state"] != "complete"
    assert unmeasured["bytes_freed"] == 0
    assert unmeasured["actions_succeeded"] >= 1
    assert unmeasured["actions_with_bytes_evidence"] == 0
    assert unmeasured["raw"]["measured_reclaim_gb"] is None
    assert unmeasured["raw"]["bytes_freed_by_engine"] == {}

    measured = by_date[measured_date]
    assert measured["measurement_state"] == "complete"
    assert measured["raw"]["bytes_freed_by_engine"] == {
        "native": {"bytes": 5 * 1024**3, "actions_measured": 1}
    }


def test_history_rejects_an_unbounded_date_range(session: Session) -> None:
    client = _client(session)
    response = client.get(
        "/api/v1/impact/history",
        params={"since": "1970-01-01", "until": "2026-09-28"},
        headers=VIEWER_HEADERS,
    )
    assert response.status_code == 422


def test_history_requires_a_viewer(session: Session) -> None:
    client = _client(session)
    response = client.get(
        "/api/v1/impact/history",
        params={"since": "2026-09-27", "until": "2026-09-27"},
    )
    assert response.status_code == 401
