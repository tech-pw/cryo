from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from cryo.db.models import AuditLog
from cryo.maintenance.impact import table_maintenance_impact


def _audit(
    table_id: uuid.UUID,
    *,
    ts: datetime,
    action: str,
    status: str = "OK",
    freed: int | None = None,
    files: tuple[int, int] | None = None,
) -> AuditLog:
    return AuditLog(
        table_id=table_id,
        ts=ts,
        action=action,
        status=status,
        actual_bytes_freed=freed,
        input_files_count=files[0] if files else None,
        output_files_count=files[1] if files else None,
    )


def test_impact_sums_only_measured_successes_per_window(session) -> None:
    table_id = uuid.uuid4()
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    session.add_all(
        [
            _audit(table_id, ts=now - timedelta(days=90), action="optimize", files=(500, 10)),
            _audit(table_id, ts=now - timedelta(days=2), action="optimize", files=(120, 4)),
            _audit(table_id, ts=now - timedelta(days=1), action="expire_snapshots", freed=4_000),
            _audit(table_id, ts=now - timedelta(days=1), action="remove_orphan_files"),
            _audit(
                table_id,
                ts=now - timedelta(hours=3),
                action="optimize",
                status="FAILED",
                files=(999, 1),
            ),
            _audit(table_id, ts=now - timedelta(hours=2), action="table_config_change"),
            _audit(uuid.uuid4(), ts=now, action="optimize", files=(50, 1)),
        ]
    )
    session.commit()

    recent, lifetime = table_maintenance_impact(session, table_id, now=now, window_days=30)

    assert lifetime.ops == 4
    assert lifetime.files_in == 620 and lifetime.files_out == 14
    assert lifetime.bytes_freed == 4_000
    assert lifetime.ops_with_bytes == 1
    assert lifetime.since is not None
    assert [item.operation for item in lifetime.by_operation] == [
        "optimize",
        "expire_snapshots",
        "remove_orphan_files",
    ]

    assert recent.ops == 3
    assert recent.files_in == 120 and recent.files_out == 4
    assert recent.ops_with_files == 1
    orphan = next(item for item in recent.by_operation if item.operation == "remove_orphan_files")
    assert orphan.ops == 1 and orphan.ops_with_bytes == 0


def test_impact_is_empty_without_audit_rows(session) -> None:
    recent, lifetime = table_maintenance_impact(
        session, uuid.uuid4(), now=datetime(2026, 10, 18, tzinfo=UTC)
    )
    assert recent.ops == 0 and lifetime.ops == 0
    assert lifetime.since is None
