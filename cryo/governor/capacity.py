from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from cryo.domain.enums import EngineType
from cryo.domain.models import CapacitySnapshot
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.worker.worker import DestinationConfig

logger = logging.getLogger(__name__)


def native_slot_capacity(config: Mapping[str, Any]) -> int:
    return int(config.get("slot_cap") or config.get("per_replica_cap") or 5)


def native_replica_names(config: Mapping[str, Any]) -> tuple[str, ...]:
    replicas = config.get("replicas") or ("default",)
    if isinstance(replicas, str):
        replicas = (replicas,)
    return tuple(str(replica) for replica in replicas)


def probe_native_capacity(
    config: Mapping[str, Any],
    *,
    destination_id: str | None = None,
    queue: MaintenanceQueue | None = None,
) -> CapacitySnapshot:
    """Return native subprocess capacity with queue in-flight as ``used``.

    Governor admission still passes ``local_in_flight`` separately; ``max(used, local)``
    keeps accounting conservative when both sources are present.
    """
    capacity = native_slot_capacity(config)
    used = 0
    if destination_id and queue is not None:
        used = queue.local_in_flight(destination_id)
    return CapacitySnapshot(capacity=capacity, used=used, queued=used)


def probe_native_capacity_by_replica(
    config: Mapping[str, Any],
    *,
    destination_id: str | None = None,
    queue: MaintenanceQueue | None = None,
) -> Mapping[str, CapacitySnapshot]:
    slot_cap = native_slot_capacity(config)
    in_flight: Mapping[str, int] = {}
    if destination_id and queue is not None:
        in_flight = queue.local_in_flight_by_replica(destination_id)
    return {
        replica: CapacitySnapshot(
            capacity=slot_cap,
            used=int(in_flight.get(replica, 0)),
            queued=int(in_flight.get(replica, 0)),
        )
        for replica in native_replica_names(config)
    }


class LiveCapacityProvider:
    """Probes real engine capacity by querying running jobs.

    For Trino: queries system.runtime.queries to count running OPTIMIZE statements.
    For Spark EMR: lists running EMR Serverless job runs.
    For Native: reports queue/Redis in-flight subprocess counts as ``used``.
    """

    def __init__(self, queue: MaintenanceQueue | None = None) -> None:
        self._queue = queue

    def probe(self, destination: DestinationConfig) -> Mapping[str, CapacitySnapshot | int]:
        engine_type = EngineType(destination.engine)

        if engine_type is EngineType.TRINO:
            return self._probe_trino(destination.config)
        if engine_type is EngineType.SPARK_EMR:
            return self._probe_emr(destination.config)

        if engine_type is EngineType.NATIVE:
            return self._probe_native(destination.config, destination_id=destination.id)

        return _static_capacity(destination.config)

    def _probe_native(
        self,
        config: Mapping[str, Any],
        *,
        destination_id: str | None = None,
    ) -> Mapping[str, CapacitySnapshot]:
        return probe_native_capacity_by_replica(
            config,
            destination_id=destination_id,
            queue=self._queue,
        )

    def _probe_trino(self, config: Mapping[str, Any]) -> Mapping[str, CapacitySnapshot]:
        from cryo.engines.trino.client import coordinators_from_config
        from cryo.engines.trino.load import count_running_optimizes

        coordinators = coordinators_from_config(config)
        per_replica_cap = int(config.get("per_replica_cap") or config.get("slot_cap") or 5)

        result: dict[str, CapacitySnapshot] = {}
        for coordinator in coordinators:
            try:
                used = count_running_optimizes(coordinator)
            except Exception:
                used = 0
            result[coordinator.label] = CapacitySnapshot(capacity=per_replica_cap, used=used)

        return result

    def _probe_emr(self, config: Mapping[str, Any]) -> Mapping[str, CapacitySnapshot]:
        """Probe EMR Serverless capacity by listing running jobs."""
        slot_cap = int(config.get("slot_cap") or config.get("concurrent_jobs") or 10)

        try:
            used = _count_running_emr_jobs(config)
        except Exception:
            used = 0

        return {"emr": CapacitySnapshot(capacity=slot_cap, used=used)}


def _count_running_emr_jobs(config: Mapping[str, Any]) -> int:
    """Count currently running EMR Serverless jobs for the application."""
    import boto3

    emr_config = config.get("emr", {})
    application_id = str(emr_config.get("application_id") or config.get("application_id"))
    region = str(emr_config.get("region") or config.get("region", "us-east-1"))

    client = boto3.client("emr-serverless", region_name=region)

    running_states = ["PENDING", "SCHEDULED", "RUNNING"]
    total = 0

    for state in running_states:
        paginator = client.get_paginator("list_job_runs")
        for page in paginator.paginate(applicationId=application_id, states=[state]):
            total += len(page.get("jobRuns", []))

    return total


def _static_capacity(config: Mapping[str, Any]) -> Mapping[str, CapacitySnapshot]:
    """Fallback static capacity when live probing isn't available."""
    replicas = config.get("replicas") or config.get("coordinators") or ("default",)
    if isinstance(replicas, str):
        replicas = (replicas,)
    cap = int(config.get("per_replica_cap") or config.get("slot_cap") or 5)
    return {str(replica): CapacitySnapshot(capacity=cap, used=0) for replica in replicas}


def evaluate_qpp_for_job(
    *,
    table_id: uuid.UUID | None,
    files_scanned: int,
    partition_count: int,
    cotenant_load_pct: float,
    target_p95_ms: int,
    is_cached: bool = False,
    query_text: str | None = None,
    model: Any | None = None,
) -> tuple[bool, float]:
    """Run the variance-aware QPP gate for a single maintenance job.

    Returns ``(admit, predicted_p95_ms)``. The caller is responsible for honoring the
    decision (refusing the job, deferring it, etc.). ``model`` is optional -- if not
    supplied, a fresh :class:`cryo.governor.qpp.QPPModel` is created and used in heuristic
    mode. Pass a pre-loaded model from a worker cache to amortize the training cost.
    """
    from cryo.config import get_settings
    from cryo.governor.qpp import QPPFeatures, QPPModel, hash_query_text, predict_admission

    settings = get_settings()
    if not settings.qpp_enabled:
        return True, 0.0

    if model is None:
        model = QPPModel(min_observations=settings.qpp_min_observations)
    if table_id is None:
        # No table identity means we can't evaluate the per-table variance signal; admit
        # to avoid breaking jobs that don't carry enough metadata.
        return True, 0.0

    features = QPPFeatures(
        query_text_hash=hash_query_text(query_text),
        table_id=table_id,
        partition_count=max(0, int(partition_count)),
        files_scanned=max(1, int(files_scanned)),
        is_cached=bool(is_cached),
        cotenant_load_pct=max(0.0, float(cotenant_load_pct)),
        hour_of_day=0,
        day_of_week=0,
    )
    admit, prediction = predict_admission(
        model,
        features,
        target_p95_ms=int(target_p95_ms),
    )
    if not admit:
        logger.info(
            "QPP refuse: table=%s files=%d parts=%d load=%.2f predicted_p95=%.0fms target=%dms",
            table_id,
            files_scanned,
            partition_count,
            cotenant_load_pct,
            prediction.p95_ms,
            target_p95_ms,
        )
    return admit, prediction.p95_ms
