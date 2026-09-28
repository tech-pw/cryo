from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.api.routes_audit import AUDIT_LIST_MAX, list_fleet_audit
from cryo.db.models import AuditLog


def _row(
    *,
    ts: datetime,
    action: str,
    status: str = "succeeded",
    engine: str | None = "spark",
    actual_bytes_freed: int | None = None,
) -> AuditLog:
    return AuditLog(
        ts=ts,
        action=action,
        status=status,
        engine=engine,
        actual_bytes_freed=actual_bytes_freed,
        input_files_count=None,
        output_files_count=None,
    )


def test_fleet_audit_filters_newest_first_and_keeps_unknown_bytes(session) -> None:
    now = datetime(2026, 9, 28, 12, tzinfo=UTC)
    session.add_all(
        [
            _row(ts=now - timedelta(hours=2), action="optimize", engine="trino"),
            _row(ts=now, action="expire_snapshots", engine="spark", actual_bytes_freed=None),
            _row(ts=now - timedelta(hours=1), action="optimize", status="failed", engine="spark"),
        ]
    )
    session.commit()

    spark = list_fleet_audit(session, limit=50, engine="spark")
    assert [item.action for item in spark.items] == ["expire_snapshots", "optimize"]
    assert spark.items[0].actual_bytes_freed is None
    assert spark.items[0].effect_status == "unverified"

    failed = list_fleet_audit(session, limit=50, status="failed")
    assert [item.action for item in failed.items] == ["optimize"]
    assert failed.items[0].status == "failed"


def test_fleet_audit_caps_the_page(session) -> None:
    now = datetime(2026, 9, 28, tzinfo=UTC)
    session.add_all(
        [
            _row(ts=now - timedelta(seconds=index), action=f"op-{index}")
            for index in range(AUDIT_LIST_MAX + 5)
        ]
    )
    session.commit()

    page = list_fleet_audit(session, limit=AUDIT_LIST_MAX + 50)
    assert page.limit == AUDIT_LIST_MAX
    assert len(page.items) == AUDIT_LIST_MAX
    assert page.items[0].action == "op-0"
