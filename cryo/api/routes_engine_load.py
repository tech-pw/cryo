from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.api.deps import SessionDep, require_viewer
from cryo.db.models import Destination, MaintenanceJob
from cryo.governor.admission import AdmissionConfig

router = APIRouter(prefix="/api/v1", tags=["runs"])

_OCCUPYING = ("admitted", "running")


class EngineLoadRow(BaseModel):
    destination_id: UUID
    name: str
    engine: str
    slots_used: int
    slots_limit: int
    admission: str
    live_probe: str = "not recorded"


class EngineLoadResponse(BaseModel):
    engines: list[EngineLoadRow]


def engine_load_rows(
    destinations: list[tuple[UUID, str, str, dict | None]],
    used_by_destination: dict[UUID, int],
) -> list[EngineLoadRow]:
    rows: list[EngineLoadRow] = []
    for destination_id, name, engine, config in destinations:
        raw_cap = (config or {}).get("per_replica_cap")
        default_cap = AdmissionConfig().per_replica_cap
        limit = raw_cap if isinstance(raw_cap, int) and raw_cap > 0 else default_cap
        used = used_by_destination.get(destination_id, 0)
        rows.append(
            EngineLoadRow(
                destination_id=destination_id,
                name=name,
                engine=engine,
                slots_used=used,
                slots_limit=limit,
                admission="throttled" if used >= limit else "open",
            )
        )
    return rows


def load_engine_admission(session: Session) -> EngineLoadResponse:
    destinations = session.execute(
        select(Destination.id, Destination.name, Destination.engine, Destination.config).where(
            Destination.enabled.is_(True)
        )
    ).all()
    counts: dict[UUID, int] = {
        destination_id: count
        for destination_id, count in session.execute(
            select(MaintenanceJob.destination_id, func.count())
            .where(MaintenanceJob.status.in_(_OCCUPYING))
            .group_by(MaintenanceJob.destination_id)
        ).all()
    }
    return EngineLoadResponse(
        engines=engine_load_rows(
            [(row.id, row.name, row.engine, row.config) for row in destinations],
            counts,
        )
    )


@router.get(
    "/engine-load",
    response_model=EngineLoadResponse,
    dependencies=[Depends(require_viewer)],
)
def get_engine_load(session: SessionDep) -> EngineLoadResponse:
    return load_engine_admission(session)
