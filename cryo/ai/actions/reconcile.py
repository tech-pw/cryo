"""Map a proposal onto the maintenance run it already created. Never invent success."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AssistantActionProposal, JsonList, MaintenanceJob, MaintenanceRun


def reconcile_proposal(
    session: Session,
    proposal: AssistantActionProposal,
) -> AssistantActionProposal:
    """Set status from the linked run row. There is no status argument."""
    if proposal.run_id is None:
        return proposal
    run = session.get(MaintenanceRun, proposal.run_id)
    if run is None:
        return proposal
    jobs = list(
        session.scalars(select(MaintenanceJob).where(MaintenanceJob.run_id == run.id)).all()
    )
    proposal.job_ids = cast("JsonList", [str(job.id) for job in jobs])
    proposal.status = _observed_status(run.status, {job.status for job in jobs})
    proposal.updated_at = datetime.now(UTC)
    session.commit()
    return proposal


def _observed_status(run_status: str, job_statuses: set[str]) -> str:
    if run_status == "succeeded":
        return "succeeded"
    if run_status == "partial" or (
        "failed" in job_statuses and "succeeded" in job_statuses and run_status != "succeeded"
    ):
        return "partial"
    if run_status == "failed" or run_status in {"cancelled", "canceled"}:
        return "failed"
    if run_status == "running" or "running" in job_statuses or "admitted" in job_statuses:
        return "running"
    return "queued"
