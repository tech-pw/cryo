from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from cryo.api.deps import SessionDep, require_operator
from cryo.evidence.mdr import build_maintenance_decision_record

router = APIRouter(prefix="/api/v1/evidence", tags=["evidence"])


@router.get(
    "/maintenance-decisions/{intent_id}",
    response_model=dict[str, Any],
    dependencies=[Depends(require_operator)],
)
def get_maintenance_decision_record(
    intent_id: uuid.UUID,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        return build_maintenance_decision_record(session, intent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
