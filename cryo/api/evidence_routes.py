from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from cryo.api.deps import SessionDep, require_admin, require_operator
from cryo.evidence.mdr import build_maintenance_decision_record
from cryo.intelligence.outcomes import build_outcome_dataset
from cryo.privacy.fleet_benchmark import build_aggregate_fleet_benchmark

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


@router.get(
    "/aggregate-fleet-benchmark",
    response_model=dict[str, Any],
    dependencies=[Depends(require_admin)],
)
def get_aggregate_fleet_benchmark(
    session: SessionDep,
    min_distinct_tables: int = Query(default=10, ge=10, le=100),
    min_outcomes: int = Query(default=20, ge=20, le=500),
) -> dict[str, object]:
    try:
        report = build_aggregate_fleet_benchmark(
            build_outcome_dataset(session, limit=500),
            min_distinct_tables=min_distinct_tables,
            min_outcomes=min_outcomes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return report.to_dict()
