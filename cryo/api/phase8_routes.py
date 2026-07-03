from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from cryo.api.deps import require_admin, require_operator, require_viewer
from cryo.api.schemas import JsonDict
from cryo.db.models import Preset, Table, TablePartition, TierPolicyRecord
from cryo.db.repositories.presets import preset_to_config, table_to_candidate
from cryo.db.session import get_session
from cryo.scheduler.adaptive_routing import (
    AdaptiveRoutingContext,
    DestinationSignal,
    rank_destinations,
    resolve_destination_adaptive,
)
from cryo.scheduler.routing import build_destinations_index
from cryo.sources.latency import get_catalog_latency_monitor
from cryo.tiering.service import (
    policies_from_records,
    summarize_tier_savings,
    tier_candidates_for_table,
)

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/v1", tags=["phase8"])


class TierPolicyCreate(BaseModel):
    source_id: uuid.UUID
    name: str
    storage_class: str
    min_age_days: int = Field(ge=1)
    predicate: JsonDict | None = None
    enabled: bool = True


class TierPolicyRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    name: str
    storage_class: str
    min_age_days: int
    predicate: JsonDict | None = None
    enabled: bool


class TierPolicyPatch(BaseModel):
    name: str | None = None
    storage_class: str | None = None
    min_age_days: int | None = Field(default=None, ge=1)
    predicate: JsonDict | None = None
    enabled: bool | None = None


class TierCandidateRead(BaseModel):
    table_ref: str
    partition_value: str | None
    age_days: int
    target_storage_class: str


class TierCandidatesResponse(BaseModel):
    table_id: uuid.UUID
    candidates: list[TierCandidateRead]
    summary: JsonDict


class RoutingPreviewRequest(BaseModel):
    table_id: uuid.UUID
    preset_id: uuid.UUID


class RankedDestinationRead(BaseModel):
    destination_id: str
    engine: str
    score: float
    reason: str


class RoutingPreviewResponse(BaseModel):
    table_id: uuid.UUID
    preset_id: uuid.UUID
    selected_destination_id: str
    selected_reason: str
    ranked_destinations: list[RankedDestinationRead]


@router.get(
    "/tier-policies",
    response_model=list[TierPolicyRead],
    dependencies=[Depends(require_viewer)],
)
def list_tier_policies(
    session: SessionDep,
    source_id: uuid.UUID | None = None,
) -> list[TierPolicyRecord]:
    query = select(TierPolicyRecord).order_by(TierPolicyRecord.name)
    if source_id is not None:
        query = query.where(TierPolicyRecord.source_id == source_id)
    return list(session.scalars(query))


@router.post(
    "/tier-policies",
    response_model=TierPolicyRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_tier_policy(payload: TierPolicyCreate, session: SessionDep) -> TierPolicyRecord:
    row = TierPolicyRecord(**payload.model_dump())
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.patch(
    "/tier-policies/{policy_id}",
    response_model=TierPolicyRead,
    dependencies=[Depends(require_admin)],
)
def patch_tier_policy(
    policy_id: uuid.UUID,
    payload: TierPolicyPatch,
    session: SessionDep,
) -> TierPolicyRecord:
    row = session.get(TierPolicyRecord, policy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tier policy not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    session.commit()
    session.refresh(row)
    return row


@router.delete(
    "/tier-policies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_tier_policy(policy_id: uuid.UUID, session: SessionDep) -> None:
    row = session.get(TierPolicyRecord, policy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tier policy not found")
    session.delete(row)
    session.commit()


@router.get(
    "/tables/{table_id}/tier-candidates",
    response_model=TierCandidatesResponse,
    dependencies=[Depends(require_viewer)],
)
def get_tier_candidates(table_id: uuid.UUID, session: SessionDep) -> TierCandidatesResponse:
    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")

    policies = session.scalars(
        select(TierPolicyRecord).where(
            TierPolicyRecord.source_id == table.source_id,
            TierPolicyRecord.enabled.is_(True),
        )
    )
    policy_rows = [
        {
            "name": row.name,
            "storage_class": row.storage_class,
            "min_age_days": row.min_age_days,
            "predicate": row.predicate,
            "enabled": row.enabled,
        }
        for row in policies
    ]
    latest_scan = session.scalar(
        select(TablePartition.scan_at)
        .where(TablePartition.table_id == table_id)
        .order_by(desc(TablePartition.scan_at))
        .limit(1)
    )
    partitions: list[dict[str, Any]] = []
    if latest_scan is not None:
        rows = session.scalars(
            select(TablePartition).where(
                TablePartition.table_id == table_id,
                TablePartition.scan_at == latest_scan,
            )
        )
        partitions = [
            {
                "partition_value": row.partition_value,
                "last_write_at": (row.raw or {}).get("last_write_at"),
                **dict(row.raw or {}),
            }
            for row in rows
        ]

    table_ref = f"iceberg.{table.schema_name}.{table.table_name}"
    candidates = tier_candidates_for_table(
        table_ref,
        partitions,
        policies_from_records(policy_rows),
    )
    summary = summarize_tier_savings(candidates)
    return TierCandidatesResponse(
        table_id=table_id,
        candidates=[
            TierCandidateRead(
                table_ref=c.table_ref,
                partition_value=c.partition_value,
                age_days=c.age_days,
                target_storage_class=c.target_storage_class,
            )
            for c in candidates
        ],
        summary=summary,
    )


@router.post(
    "/routing/preview",
    response_model=RoutingPreviewResponse,
    dependencies=[Depends(require_operator)],
)
def preview_routing(payload: RoutingPreviewRequest, session: SessionDep) -> RoutingPreviewResponse:
    table = session.get(Table, table_id := payload.table_id)
    preset_row = session.get(Preset, payload.preset_id)
    if table is None or preset_row is None:
        raise HTTPException(status_code=404, detail="Table or preset not found")

    preset = preset_to_config(preset_row)
    candidate = table_to_candidate(table)
    destinations = build_destinations_index(_list_destinations(session))
    monitor = get_catalog_latency_monitor(str(table.source_id))
    latency = monitor.summary("load_table")
    signals = [
        DestinationSignal(
            destination_id=entry["id"],
            engine=entry["engine"],
            available_slots=int(
                entry.get("config", {}).get("per_replica_cap")
                or entry.get("config", {}).get("slot_cap")
                or 5
            ),
            catalog_p99_ms=float(latency.get("p99_ms") or 0),
        )
        for entry in _list_destinations(session)
    ]
    context = AdaptiveRoutingContext(
        signals=signals, catalog_p99_ms=float(latency.get("p99_ms") or 0)
    )
    route = resolve_destination_adaptive(candidate, preset, destinations, context)
    ranked = rank_destinations(candidate, preset, destinations, context)
    return RoutingPreviewResponse(
        table_id=table_id,
        preset_id=payload.preset_id,
        selected_destination_id=route.destination_id,
        selected_reason=route.reason,
        ranked_destinations=[
            RankedDestinationRead(
                destination_id=item.destination_id,
                engine=item.engine,
                score=item.score,
                reason=item.reason,
            )
            for item in ranked
        ],
    )


def _list_destinations(session: Session) -> list[dict[str, Any]]:
    from cryo.db.models import Destination

    return [
        {
            "id": str(row.id),
            "name": row.name,
            "engine": row.engine,
            "config": dict(row.config),
            "enabled": row.enabled,
        }
        for row in session.scalars(select(Destination).where(Destination.enabled.is_(True)))
    ]
