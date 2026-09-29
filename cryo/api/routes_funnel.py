from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.api.deps import SessionDep, require_viewer
from cryo.db.models import MaintenanceRun
from cryo.maintenance.funnel import decision_funnel
from cryo.maintenance.skip_history import SkipEvaluation

router = APIRouter(prefix="/api/v1", tags=["decisions"])


class FunnelStage(BaseModel):
    stage: str
    count: int | None
    reason: str | None


class DecisionFunnelResponse(BaseModel):
    run_id: str | None
    stages: list[FunnelStage]


def latest_batch_funnel(session: Session) -> DecisionFunnelResponse:
    runs = session.scalars(
        select(MaintenanceRun).order_by(MaintenanceRun.created_at.desc()).limit(20)
    ).all()
    for run in runs:
        summary = run.summary if isinstance(run.summary, dict) else None
        if summary and summary.get("pipeline") == "batch":
            grouped = session.execute(
                select(SkipEvaluation.stage, func.count())
                .where(SkipEvaluation.run_id == run.id)
                .group_by(SkipEvaluation.stage)
            ).all()
            history = [{"stage": stage, "count": count} for stage, count in grouped]
            return DecisionFunnelResponse(
                run_id=str(run.id),
                stages=[
                    FunnelStage.model_validate(row)
                    for row in decision_funnel(summary, history or None)
                ],
            )
    empty = [FunnelStage.model_validate(row) for row in decision_funnel(None)]
    return DecisionFunnelResponse(run_id=None, stages=empty)


@router.get(
    "/decisions/funnel",
    response_model=DecisionFunnelResponse,
    dependencies=[Depends(require_viewer)],
)
def get_decision_funnel(session: SessionDep) -> DecisionFunnelResponse:
    return latest_batch_funnel(session)
