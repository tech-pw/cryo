"""API routes for dark/zombie table detection (cryo.telemetry.dark_tables).

Endpoints (all under ``/api/v1``):

- ``GET  /dark-tables``                    -- confirmed candidates (>= DARK_TABLE_CONFIRMATION_
                                               WINDOWS consecutive zero-query days), ranked by
                                               storage size -- the biggest, most confident
                                               waste first.
- ``POST /dark-tables/{table_id}/dismiss``  -- operator confirms a table is fine (e.g. a
                                               quarterly-report table) and shouldn't keep
                                               surfacing. Detection resumes normally afterward
                                               if it ever goes quiet again after a real query.

Detection itself runs on the scheduler's daily tick (cryo.scheduler.service._maybe_detect_
dark_tables); there is no POST /run here because, unlike the dedup pipeline, this has nothing
expensive to trigger on demand -- it only reads TableStats.query_count_30d, already computed.

Like routes_dedup.py, nothing under this router deletes or archives table data. status can only
become 'dismissed' here (an operator judgment call, not a system action) -- 'archived' is
reserved for a separate, not-yet-built, explicitly opt-in action.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from cryo.api.deps import SessionDep, require_operator, require_viewer
from cryo.api.schemas import (
    DarkTableCandidateRead,
    DarkTableCandidatesResponse,
    DarkTableDismissRequest,
    DarkTableDismissResponse,
)
from cryo.api.security import Principal
from cryo.db.models import DarkTableCandidate, Table
from cryo.telemetry.dark_tables import list_confirmed_dark_table_candidates

router = APIRouter(prefix="/api/v1", tags=["dark-tables"])

OperatorPrincipalDep = Annotated[Principal, Depends(require_operator)]


@router.get(
    "/dark-tables",
    response_model=DarkTableCandidatesResponse,
    dependencies=[Depends(require_viewer)],
)
def list_dark_tables(session: SessionDep) -> DarkTableCandidatesResponse:
    candidates = list_confirmed_dark_table_candidates(session)
    if not candidates:
        return DarkTableCandidatesResponse(candidates=[])

    table_ids = [c.table_id for c in candidates]
    tables = {
        t.id: t
        for t in session.execute(select(Table).where(Table.id.in_(table_ids))).scalars().all()
    }

    reads: list[DarkTableCandidateRead] = []
    for candidate in candidates:
        table = tables.get(candidate.table_id)
        if table is None:
            # Table row vanished between the confirmed-candidates query and here (deleted
            # concurrently) -- detect_dark_table_candidates' next run will clean up the
            # orphaned candidate row; skip it here rather than 500ing the whole list.
            continue
        reads.append(
            DarkTableCandidateRead(
                id=candidate.id,
                table_id=candidate.table_id,
                schema_name=table.schema_name,
                table_name=table.table_name,
                consecutive_zero_query_windows=candidate.consecutive_zero_query_windows,
                first_zero_query_at=candidate.first_zero_query_at,
                last_checked_at=candidate.last_checked_at,
                total_size_gb=candidate.total_size_gb,
                estimated_monthly_cost_usd=candidate.estimated_monthly_cost_usd,
                status=candidate.status,
            )
        )

    return DarkTableCandidatesResponse(
        candidates=reads,
        total_candidates=len(reads),
        total_size_gb=sum(r.total_size_gb for r in reads),
        total_estimated_monthly_cost_usd=sum(r.estimated_monthly_cost_usd or 0.0 for r in reads),
    )


@router.post(
    "/dark-tables/{table_id}/dismiss",
    response_model=DarkTableDismissResponse,
)
def dismiss_dark_table(
    table_id: UUID,
    payload: DarkTableDismissRequest,
    session: SessionDep,
    principal: OperatorPrincipalDep,
) -> DarkTableDismissResponse:
    candidate = session.execute(
        select(DarkTableCandidate).where(DarkTableCandidate.table_id == table_id)
    ).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Dark table candidate not found")

    now = datetime.now(UTC)
    candidate.status = "dismissed"
    candidate.dismissed_by = principal.subject
    candidate.dismissed_at = now
    candidate.dismissed_reason = payload.reason
    candidate.updated_at = now
    session.commit()

    return DarkTableDismissResponse(
        table_id=table_id,
        status=candidate.status,
        dismissed_by=candidate.dismissed_by,
        dismissed_at=candidate.dismissed_at,
        dismissed_reason=candidate.dismissed_reason,
    )
