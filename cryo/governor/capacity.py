from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cryo.domain.enums import EngineType
from cryo.domain.models import CapacitySnapshot
from cryo.worker.worker import DestinationConfig


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

        return _static_capacity(destination.config)

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
