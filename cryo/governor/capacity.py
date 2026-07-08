from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from cryo.domain.enums import EngineType
from cryo.domain.models import CapacitySnapshot
from cryo.worker.worker import DestinationConfig

logger = logging.getLogger(__name__)


class LiveCapacityProvider:
    """Probes real engine capacity by querying running jobs.

    For Trino: queries system.runtime.queries to count running OPTIMIZE statements.
    For Spark EMR: lists running EMR Serverless job runs.
    """

    def probe(self, destination: DestinationConfig) -> Mapping[str, CapacitySnapshot | int]:
        engine_type = EngineType(destination.engine)

        if engine_type is EngineType.TRINO:
            return self._probe_trino(destination.config)
        if engine_type is EngineType.SPARK_EMR:
            return self._probe_emr(destination.config)

        if engine_type is EngineType.NATIVE:
            return self._probe_native(destination.config)

        return _static_capacity(destination.config)

    def _probe_native(self, config: Mapping[str, Any]) -> Mapping[str, CapacitySnapshot]:
        """Native subprocess capacity uses slot_cap; governor subtracts queue in-flight."""
        slot_cap = int(config.get("slot_cap") or config.get("per_replica_cap") or 5)
        replicas = config.get("replicas") or ("default",)
        if isinstance(replicas, str):
            replicas = (replicas,)
        return {str(replica): CapacitySnapshot(capacity=slot_cap, used=0) for replica in replicas}

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
