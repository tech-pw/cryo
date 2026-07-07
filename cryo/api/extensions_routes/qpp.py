"""Variance-aware Query Performance Prediction (QPP) routes.

Implements the CLOUD 2026 / arXiv:2606.03464 design for predicting runtime
p50/p95/p99 of a hypothetical maintenance query and gating admission.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import func, select

from cryo.api.deps import SessionDep, require_viewer
from cryo.config import get_settings

router = APIRouter(prefix="/api/v1", tags=["governor"])


class QPPFeaturesPayload(BaseModel):
    partition_count: int = 0
    files_scanned: int = 1
    is_cached: bool = False
    cotenant_load_pct: float = 0.0
    hour_of_day: int = 0
    day_of_week: int = 0


class QPPPredictRequest(BaseModel):
    table_id: uuid.UUID
    query_text: str | None = None
    features: QPPFeaturesPayload


class QPPPredictResponse(BaseModel):
    table_id: uuid.UUID
    p50_ms: float
    p95_ms: float
    p99_ms: float
    confidence: float
    admit: bool
    target_p95_ms: int
    exceeded_tolerance: bool


class QPPObservationPayload(BaseModel):
    table_id: uuid.UUID
    actual_p95_ms: float
    features: QPPFeaturesPayload


class QPPConfidenceResponse(BaseModel):
    observations: int
    confidence: float
    min_observations: int
    using_trained_model: bool
    qpp_enabled: bool


def _build_qpp_model(session: SessionDep, settings: Any) -> Any:
    """Construct a :class:`QPPModel` and pre-train it from recent observations.

    Returns a model whose confidence reflects how much history it has -- callers that
    want to gate admissions on a low-confidence prediction should treat the result as
    advisory.
    """
    from cryo.governor.qpp import QPPModel, QPPStore

    model = QPPModel(min_observations=settings.qpp_min_observations)
    try:
        store = QPPStore(
            session_factory=_session_factory_closure(session),
            min_observations=settings.qpp_min_observations,
        )
    except Exception:
        return model
    rows = store.load_recent(limit=model.window_size)
    if rows:
        model.train(rows)
    return model


def _session_factory_closure(_session: Any) -> Any:
    """Build a callable that returns fresh sessions for the :class:`QPPStore`.

    We import lazily so this module has no hard dependency on the engine layer at import
    time.
    """
    from cryo.db.session import SessionLocal

    def _factory() -> Any:
        return SessionLocal()

    return _factory


@router.post(
    "/governor/qpp/predict",
    response_model=QPPPredictResponse,
    dependencies=[Depends(require_viewer)],
)
def qpp_predict(payload: QPPPredictRequest, session: SessionDep) -> QPPPredictResponse:
    """Predict runtime p50/p95/p99 for a hypothetical maintenance query."""
    from cryo.governor.qpp import QPPFeatures, hash_query_text, predict_admission

    settings = get_settings()
    model = _build_qpp_model(session, settings)
    features = QPPFeatures(
        query_text_hash=hash_query_text(payload.query_text),
        table_id=payload.table_id,
        partition_count=payload.features.partition_count,
        files_scanned=payload.features.files_scanned,
        is_cached=payload.features.is_cached,
        cotenant_load_pct=payload.features.cotenant_load_pct,
        hour_of_day=payload.features.hour_of_day,
        day_of_week=payload.features.day_of_week,
    )
    target = int(settings.qpp_target_p95_ms)
    admit, prediction = predict_admission(model, features, target_p95_ms=target)
    return QPPPredictResponse(
        table_id=payload.table_id,
        p50_ms=prediction.p50_ms,
        p95_ms=prediction.p95_ms,
        p99_ms=prediction.p99_ms,
        confidence=prediction.confidence,
        admit=admit,
        target_p95_ms=target,
        exceeded_tolerance=not admit,
    )


@router.post(
    "/governor/qpp/record",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_viewer)],
)
def qpp_record(payload: QPPObservationPayload, session: SessionDep) -> dict[str, Any]:
    """Persist a single QPP observation for future trainer use."""
    from cryo.governor.qpp import QPPFeatures, QPPStore, hash_query_text

    settings = get_settings()
    store = QPPStore(
        session_factory=_session_factory_closure(session),
        min_observations=settings.qpp_min_observations,
    )
    features = QPPFeatures(
        query_text_hash=hash_query_text(None),
        table_id=payload.table_id,
        partition_count=payload.features.partition_count,
        files_scanned=payload.features.files_scanned,
        is_cached=payload.features.is_cached,
        cotenant_load_pct=payload.features.cotenant_load_pct,
        hour_of_day=payload.features.hour_of_day,
        day_of_week=payload.features.day_of_week,
    )
    row_id = store.record_observation(features, actual_p95_ms=payload.actual_p95_ms)
    return {"id": str(row_id)}


@router.get(
    "/governor/qpp/confidence",
    response_model=QPPConfidenceResponse,
    dependencies=[Depends(require_viewer)],
)
def qpp_confidence(
    table_id: uuid.UUID | None = None,
    session: SessionDep = None,  # type: ignore[assignment]
) -> QPPConfidenceResponse:
    """Return the current model's confidence interval, optionally scoped to one table."""
    from cryo.db.models import QPPObservation

    settings = get_settings()
    model = _build_qpp_model(session, settings)
    stmt = select(func.count()).select_from(QPPObservation)
    if table_id is not None:
        stmt = stmt.where(QPPObservation.table_id == table_id)
    with _session_factory_closure(session)() as probe_session:
        count = probe_session.scalar(stmt) or 0
    return QPPConfidenceResponse(
        observations=int(count),
        confidence=float(model.confidence()),
        min_observations=int(settings.qpp_min_observations),
        using_trained_model=model._last_train_size >= settings.qpp_min_observations,
        qpp_enabled=bool(settings.qpp_enabled),
    )
