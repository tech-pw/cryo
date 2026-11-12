"""Parked async Spark jobs, for the run console and the capacity card (#491)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from cryo.api.deps import require_viewer
from cryo.config import get_settings

router = APIRouter(prefix="/api/v1/async-spark", tags=["runs"])

# A parked job whose check is this many resolver intervals late is flagged.
_OVERDUE_INTERVALS = 3


class AsyncSparkSummary(BaseModel):
    enabled: bool
    parked: int | None
    overdue: int | None
    max_in_flight_per_worker: int
    poll_interval_seconds: float


class ParkedJobsQuery(BaseModel):
    job_ids: list[str] = Field(max_length=500)


class ParkedJobRead(BaseModel):
    job_id: str
    step_index: int
    spark_ref: str | None
    parked_at: float | None
    last_checked_at: float | None
    next_check_at: float | None
    overdue: bool


class ParkedJobsResponse(BaseModel):
    jobs: list[ParkedJobRead]


def _registry() -> Any | None:
    try:
        from cryo.infra.wiring import build_runtime_wiring
        from cryo.queue.async_spark import RedisAsyncSparkRegistry

        redis_client = build_runtime_wiring().redis_client
        return RedisAsyncSparkRegistry(redis_client) if redis_client is not None else None
    except Exception:  # noqa: BLE001 - Redis down shows as "unavailable", not an error page
        return None


def _overdue_before(now: float) -> float:
    return now - _OVERDUE_INTERVALS * get_settings().async_spark_poll_interval_seconds


@router.get("/summary", response_model=AsyncSparkSummary, dependencies=[Depends(require_viewer)])
def async_spark_summary() -> AsyncSparkSummary:
    settings = get_settings()
    registry = _registry()
    parked = overdue = None
    if registry is not None:
        try:
            parked = registry.parked_count()
            overdue = registry.overdue_count(_overdue_before(time.time()))
        except Exception:  # noqa: BLE001
            parked = overdue = None
    return AsyncSparkSummary(
        enabled=settings.worker_async_spark_dispatch,
        parked=parked,
        overdue=overdue,
        max_in_flight_per_worker=settings.worker_async_spark_max_in_flight,
        poll_interval_seconds=settings.async_spark_poll_interval_seconds,
    )


@router.post("/jobs", response_model=ParkedJobsResponse, dependencies=[Depends(require_viewer)])
def parked_jobs(query: ParkedJobsQuery) -> ParkedJobsResponse:
    """Which of these jobs are parked on Spark, and when they were last checked."""
    registry = _registry()
    if registry is None or not query.job_ids:
        return ParkedJobsResponse(jobs=[])
    try:
        found = registry.describe(query.job_ids)
    except Exception:  # noqa: BLE001
        return ParkedJobsResponse(jobs=[])
    cutoff = _overdue_before(time.time())
    return ParkedJobsResponse(
        jobs=[
            ParkedJobRead(
                job_id=job.job_id,
                step_index=job.step_index,
                spark_ref=job.spark_ref,
                parked_at=job.parked_at,
                last_checked_at=job.last_checked_at,
                next_check_at=job.next_check_at,
                overdue=job.next_check_at is not None and job.next_check_at < cutoff,
            )
            for job in found.values()
        ]
    )
