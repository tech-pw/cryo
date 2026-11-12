"""Why an active job waits, and a rough ETA, for the run grid."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from tests.auth_helpers import VIEWER_HEADERS


def _seed(api_db, *, status: str, history: int = 3, error: str | None = None):
    now = datetime.now(UTC)
    source = Source(id=uuid4(), name=f"s-{uuid4()}", type="rest", config={})
    destination = Destination(id=uuid4(), name=f"d-{uuid4()}", engine="spark", config={})
    table = Table(id=uuid4(), source_id=source.id, schema_name="a", table_name="t", is_active=True)
    run = MaintenanceRun(id=uuid4(), kind="adhoc", trigger="manual", status="running")
    rows = [source, destination, table, run]
    for i in range(history):
        rows.append(
            MaintenanceJob(
                id=uuid4(),
                run_id=run.id,
                table_id=table.id,
                destination_id=destination.id,
                mode="standard",
                status="succeeded",
                started_at=now - timedelta(minutes=30 + i),
                finished_at=now - timedelta(minutes=20 + i),
            )
        )
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status=status,
        started_at=now - timedelta(minutes=4) if status == "running" else None,
        error=error,
    )
    rows.append(job)
    with api_db.session_factory() as session:
        session.add_all(rows)
        session.commit()
    return job.id


def test_running_job_eta_uses_the_tables_typical_duration(api_db) -> None:
    job_id = _seed(api_db, status="running")
    body = api_db.client.get(f"/api/v1/jobs/{job_id}/queue-status", headers=VIEWER_HEADERS).json()
    assert body["status"] == "running"
    assert 300 <= body["eta_seconds"] <= 420


def test_queued_job_says_why_and_estimates_from_throughput(api_db) -> None:
    job_id = _seed(api_db, status="queued")
    body = api_db.client.get(f"/api/v1/jobs/{job_id}/queue-status", headers=VIEWER_HEADERS).json()
    assert body["reason"].startswith("Queued; waiting for the governor")
    assert body["jobs_ahead"] == 0
    assert body["eta_seconds"] is not None


def test_deferred_job_shows_the_gate_reason(api_db) -> None:
    job_id = _seed(api_db, status="deferred", error="table written in the last 10 minutes")
    body = api_db.client.get(f"/api/v1/jobs/{job_id}/queue-status", headers=VIEWER_HEADERS).json()
    assert body["reason"] == "Deferred: table written in the last 10 minutes"


def test_plan_operations_payload_reads_planned_and_skipped() -> None:
    from cryo.db.repositories.datalake import _plan_operations_payload

    payload = _plan_operations_payload(
        {
            "operations": [{"type": "optimize"}, {"type": "expire_snapshots"}],
            "skipped_operations": [{"operation": "remove_orphan_files", "reason": "ran recently"}],
        }
    )
    assert payload["planned_operations"] == ["optimize", "expire_snapshots"]
    assert payload["skipped_operations"] == [
        {"operation": "remove_orphan_files", "reason": "ran recently"}
    ]
    assert _plan_operations_payload(None) == {"planned_operations": [], "skipped_operations": []}
