"""Pod waiting reasons and Warning events for a running Spark-on-Kubernetes job (#488)."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from cryo.api.deps import SessionDep, require_viewer
from cryo.db.models import Destination, JobStep, MaintenanceJob

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["runs"])


class SparkPodRead(BaseModel):
    role: str
    name: str
    phase: str | None
    waiting_reason: str | None
    waiting_message: str | None
    waiting_seconds: float | None
    node: str | None
    stuck: bool


class SparkPodEventRead(BaseModel):
    pod: str
    reason: str | None
    message: str | None
    count: int
    last_seen: str | None


class SparkPodsResponse(BaseModel):
    available: bool
    reason: str | None = None
    engine_ref: str | None = None
    pods: list[SparkPodRead] = []
    events: list[SparkPodEventRead] = []
    summary: str | None = None


def _engine_ref(session: SessionDep, job: MaintenanceJob) -> str | None:
    step_ref = session.scalar(
        select(JobStep.engine_ref)
        .where(JobStep.job_id == job.id, JobStep.engine_ref.is_not(None))
        .order_by(JobStep.step_index.desc(), JobStep.started_at.desc())
        .limit(1)
    )
    return step_ref or job.engine_ref


@router.get(
    "/jobs/{job_id}/spark-pods",
    response_model=SparkPodsResponse,
    dependencies=[Depends(require_viewer)],
)
def get_spark_pods(job_id: UUID, session: SessionDep) -> SparkPodsResponse:
    job = session.get(MaintenanceJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "running":
        return SparkPodsResponse(available=False, reason="The job is not running.")
    destination = session.get(Destination, job.destination_id) if job.destination_id else None
    config = dict(destination.config or {}) if destination is not None else {}
    if destination is None or str(config.get("submit_mode") or "").lower() != "k8s":
        return SparkPodsResponse(available=False, reason="Not a Spark-on-Kubernetes job.")
    engine_ref = _engine_ref(session, job)
    if not engine_ref:
        return SparkPodsResponse(available=False, reason="No Spark Job submitted yet.")
    from cryo.engines.spark.k8s import SparkK8sConfig, _new_k8s_core_client
    from cryo.engines.spark.pod_diagnostics import step_diagnostics

    try:
        namespace = SparkK8sConfig.from_config(config).namespace
        diagnostics = step_diagnostics(_new_k8s_core_client(), namespace, engine_ref)
    except Exception:  # noqa: BLE001 - no cluster access must not break the run page
        logger.warning("Could not read Spark pods for job %s", job_id, exc_info=True)
        return SparkPodsResponse(
            available=False,
            engine_ref=engine_ref,
            reason="Could not read pods from Kubernetes. Check the API's RBAC for pods and events.",
        )
    return SparkPodsResponse(available=True, **diagnostics.as_dict())
