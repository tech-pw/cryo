from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.db.models import MaintenanceRun
from cryo.db.repositories import presets


def _scan_run(**summary_overrides: object) -> MaintenanceRun:
    summary = {
        "scope": "all",
        "unreadable_tables": ["cdp.a.t1", "cdp.a.t2"],
        "failed_tables": ["cdp.b.t3"],
        "chunks_total": 4,
        "chunks_done": 4,
    }
    summary.update(summary_overrides)
    return MaintenanceRun(
        id=uuid.uuid4(),
        kind="scan",
        status="succeeded",
        created_at=datetime(2026, 10, 6, tzinfo=UTC),
        summary=summary,
    )


def test_run_summary_list_view_drops_per_table_name_lists() -> None:
    run = _scan_run()

    payload = presets.run_summary(run)

    assert "unreadable_tables" not in payload["summary"]
    assert "failed_tables" not in payload["summary"]
    # Scalar fields a list view does use stay in place.
    assert payload["summary"]["chunks_total"] == 4
    assert payload["summary"]["chunks_done"] == 4


def test_run_summary_detail_view_keeps_per_table_name_lists() -> None:
    run = _scan_run()

    payload = presets.run_summary(run, detail=True)

    assert payload["summary"]["unreadable_tables"] == ["cdp.a.t1", "cdp.a.t2"]
    assert payload["summary"]["failed_tables"] == ["cdp.b.t3"]
