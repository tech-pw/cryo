"""Tests for the checklist API helper functions in cryo/api/extensions.py -- specifically
_latest_preset_idle_hours, which has to correctly navigate the real MaintenanceJob.plan shape
(operations: [{type, params}, ...], not a flat top-level params key)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.api.extensions import _latest_preset_idle_hours
from cryo.db.models import MaintenanceJob, MaintenanceRun, Source, Table


def _seed_table_with_job(session, *, plan: dict | None) -> Table:
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    session.add(Source(id=source_id, name=f"src-{source_id}", type="rest", config={}))
    table = Table(id=table_id, source_id=source_id, schema_name="demo", table_name="events", is_active=True)
    session.add(table)
    session.flush()
    run = MaintenanceRun(id=uuid.uuid4(), kind="adhoc", trigger="api", status="succeeded")
    session.add(run)
    session.flush()
    session.add(MaintenanceJob(
        id=uuid.uuid4(), run_id=run.id, table_id=table_id, destination_id=uuid.uuid4(),
        mode="standard", status="succeeded", started_at=datetime.now(UTC), plan=plan,
    ))
    session.commit()
    return table


def test_latest_preset_idle_hours_reads_from_operations_params(session) -> None:
    plan = {
        "mode": "standard",
        "notes": [],
        "operations": [
            {"type": "optimize", "params": {"partition_write_idle_hours": 72, "file_size_mb": 256}},
        ],
    }
    table = _seed_table_with_job(session, plan=plan)
    assert _latest_preset_idle_hours(session, table.id) == 72.0


def test_latest_preset_idle_hours_none_when_no_job(session) -> None:
    table_id = uuid.uuid4()
    session.add(Source(id=uuid.uuid4(), name="src", type="rest", config={}))
    session.commit()
    assert _latest_preset_idle_hours(session, table_id) is None


def test_latest_preset_idle_hours_none_when_plan_has_no_operations(session) -> None:
    table = _seed_table_with_job(session, plan={"mode": "standard", "notes": []})
    assert _latest_preset_idle_hours(session, table.id) is None


def test_latest_preset_idle_hours_none_when_param_absent(session) -> None:
    plan = {"operations": [{"type": "expire_snapshots", "params": {"retention_days": 7}}]}
    table = _seed_table_with_job(session, plan=plan)
    assert _latest_preset_idle_hours(session, table.id) is None
